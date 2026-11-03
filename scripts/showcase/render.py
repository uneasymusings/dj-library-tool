"""Frame actual CLI captures as short silent MP4s and looping README GIFs.

Requires FFmpeg and rsvg-convert. No browser, native app, network or personal library.
"""

import argparse
import base64
import io
import json
import os
import re
import subprocess
from html import escape
from pathlib import Path

from rich.console import Console
from rich.terminal_theme import TerminalTheme
from rich.text import Text

from djlib.interfaces.terminal import THEME

ROOT = Path(__file__).parent
WIDTH, HEIGHT = 1440, 1120
INK, MUTED, AMBER = "#e2e5eb", "#9299a6", "#f5af40"
SVG_THEME = TerminalTheme(
    (17, 20, 27),
    (226, 229, 235),
    [
        (17, 20, 27),
        (235, 105, 112),
        (126, 204, 153),
        (245, 183, 73),
        (119, 159, 222),
        (182, 147, 220),
        (114, 195, 202),
        (226, 229, 235),
    ],
    [
        (111, 121, 139),
        (246, 133, 140),
        (153, 222, 171),
        (255, 205, 105),
        (149, 184, 239),
        (204, 173, 240),
        (144, 216, 222),
        (255, 255, 255),
    ],
)
SVG_CODE = """<svg xmlns="http://www.w3.org/2000/svg" width="{terminal_width}"
height="{terminal_height}" viewBox="0 0 {terminal_width} {terminal_height}">
<style>.{unique_id}-matrix {{font-family:'JetBrains Mono',monospace;font-size:20px;
font-variant-ligatures:none;white-space:pre;}} {styles}</style>
<defs>{lines}</defs><g class="{unique_id}-matrix" fill="#e2e5eb">{backgrounds}{matrix}</g></svg>"""


def text(x, y, value, size=20, color=INK, bold=False, spacing=0):
    return (
        f'<text x="{x}" y="{y}" fill="{color}" font-size="{size}" '
        f'font-weight="{700 if bold else 400}" letter-spacing="{spacing}">'
        f"{escape(value)}</text>"
    )


def terminal_image(value, columns=108):
    # ANSI color spans are preserved; cursor/OSC sequences have no visual text.
    value = re.sub(r"\x1b\]8;.*?(?:\x1b\\|\x07)", "", value)
    stream = io.StringIO()
    console = Console(
        file=stream,
        width=columns,
        record=True,
        theme=THEME,
        force_terminal=True,
        color_system="truecolor",
        _environ={"TERM": "xterm-256color"},
    )
    console.print(Text.from_ansi(value.rstrip()), end="\n")
    svg = console.export_svg(code_format=SVG_CODE, theme=SVG_THEME)
    width = float(re.search(r'width="([\d.]+)"', svg)[1])
    height = float(re.search(r'height="([\d.]+)"', svg)[1])
    return svg, width, height


def image_tag(svg, x, y, width, height):
    encoded = base64.b64encode(svg.encode()).decode()
    return (
        f'<image x="{x}" y="{y}" width="{width}" height="{height}" '
        f'href="data:image/svg+xml;base64,{encoded}"/>'
    )


def board(capture, index, stage=None, command="", output="", intro=False):
    values = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}">',
        '<defs><linearGradient id="bg" x2="1" y2="1"><stop stop-color="#171b24"/>'
        '<stop offset="1" stop-color="#0b0d12"/></linearGradient></defs>',
        '<rect width="100%" height="100%" fill="url(#bg)"/>',
        '<g font-family="JetBrains Mono,monospace">',
    ]
    # A restrained lattice is visual texture, never a purported audio waveform.
    for row in range(7):
        values.append(text(1190, 56 + row * 16, ". : . : . :", 12, "#353a45"))
    values += [
        text(64, 54, "djlib / in practice", 21, AMBER, True),
        text(1110, 54, f"{index:02} / 05", 19, MUTED),
        text(64, 120, capture["name"].upper(), 56, INK, True, -1.5),
        text(67, 158, capture["subtitle"], 20, MUTED),
    ]
    labels = ["01  MATCH", "02  SHOP", "03  PREP"]
    for number, label in enumerate(labels):
        x = 64 + number * 438
        active = stage == number
        values.append(text(x, 204, label, 17, AMBER if active else "#697180", active, 1))
        values.append(
            f'<rect x="{x}" y="216" width="408" height="2" '
            f'fill="{AMBER if active else "#2a303b"}"/>'
        )
    if intro:
        values += [
            text(64, 360, "A TRACKLIST.", 70, INK, True),
            text(64, 446, "YOUR RECORDS.", 70, INK, True),
            text(64, 532, "YOUR NEXT CRATE.", 70, AMBER, True),
            text(68, 648, capture["lesson"], 25, INK),
            text(68, 710, "Published selection / generated demo library", 21, MUTED),
            text(68, 756, "Three real commands. One practical workflow.", 21, MUTED),
        ]
    else:
        values += [
            '<rect x="44" y="242" width="1352" height="778" rx="14" '
            'fill="#11141b" stroke="#2a303b"/>',
            text(68, 282, "$", 20, AMBER, True),
        ]
        prompt, pw, ph = terminal_image(command, capture["columns"])
        values.append(image_tag(prompt, 98, 262, pw, ph))
        if output:
            svg, sw, sh = terminal_image(output, capture["columns"])
            scale = min(0.94, 1240 / sw, 680 / sh)
            values.append(image_tag(svg, 68, 318, sw * scale, sh * scale))
        values.append(text(68, 1000, capture["stages"][stage]["label"], 15, "#757e8d", spacing=1))
    values += [
        text(64, 1065, "CLI REPLAY  /  DEMO LIBRARY", 18, AMBER, True),
        text(1075, 1065, f"v{capture['version']}", 17, MUTED),
        text(
            64,
            1094,
            "Selected tracks. Saved BPM/key are illustrative. No live recognition or USB export.",
            16,
            MUTED,
        ),
        "</g></svg>",
    ]
    return "".join(values)


def frame(svg, target, env):
    source = target.with_suffix(".svg")
    source.write_text(svg, encoding="utf-8")
    subprocess.run(
        ["rsvg-convert", str(source), "-o", str(target)], env=env, check=True, capture_output=True
    )


def render(capture, index, output, env, poster_only=False):
    name = capture["id"]
    folder = output / "frames" / name
    folder.mkdir(parents=True, exist_ok=True)
    sequence = []

    def add(svg, seconds, tag):
        png = folder / f"{len(sequence):03}-{tag}.png"
        frame(svg, png, env)
        sequence.append((png, seconds))
        return png

    add(board(capture, index, intro=True), 2.2, "intro")
    for stage, recording in enumerate(capture["stages"]):
        command = recording["command"]
        if not poster_only:
            for position in range(0, len(command) + 8, 8):
                add(board(capture, index, stage, command[:position] + "▌"), 0.055, "type")
        lines = recording["stdout"].strip().splitlines()
        if stage == 1:
            # The camera holds the shopping results; long saved-file paths and shell-open
            # hints remain in the original capture but add no useful detail to this cut.
            plain = [Text.from_ansi(line).plain for line in lines]
            stop = next((i for i, line in enumerate(plain) if "Saved to" in line), len(lines))
            lines = lines[:stop]
        if not poster_only:
            for count in (5, 12):
                add(board(capture, index, stage, command, "\n".join(lines[:count])), 0.16, "reveal")
        # Camera cuts preserve complete lines. The source capture is retained for auditing.
        first = "\n".join(lines[:28])
        poster = add(board(capture, index, stage, command, first), 5.5, "result")
        (
            output / f"{name}-{'prep' if stage == 2 else 'poster' if stage == 0 else 'shop'}.png"
        ).write_bytes(poster.read_bytes())
        if len(lines) > 28:
            add(board(capture, index, stage, command, "\n".join(lines[-28:])), 3.0, "continue")
    if poster_only:
        return
    concat = folder / "timeline.txt"
    concat.write_text(
        "".join(f"file '{path.as_posix()}'\nduration {seconds}\n" for path, seconds in sequence)
        + f"file '{sequence[-1][0].as_posix()}'\n",
        encoding="utf-8",
    )
    movie = output / f"demo-{name}.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat),
            "-t",
            f"{sum(seconds for _, seconds in sequence):.3f}",
            "-vf",
            "fps=30,format=yuv420p",
            "-c:v",
            "libx264",
            "-crf",
            "20",
            "-preset",
            "fast",
            "-movflags",
            "+faststart",
            str(movie),
        ],
        check=True,
    )
    palette = folder / "palette.png"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(movie),
            "-vf",
            "fps=8,scale=1080:-2:flags=lanczos,palettegen=max_colors=96",
            str(palette),
        ],
        check=True,
    )
    gif = output / f"{name}.gif"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(movie),
            "-i",
            str(palette),
            "-lavfi",
            "fps=8,scale=1080:-2:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle",
            "-loop",
            "0",
            "-final_delay",
            "150",
            str(gif),
        ],
        check=True,
    )
    print(
        f"{name}: {sum(seconds for _, seconds in sequence):.1f}s, "
        f"MP4 {movie.stat().st_size / 1e6:.2f}MB, GIF {gif.stat().st_size / 1e6:.2f}MB",
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--only")
    parser.add_argument("--poster-only", action="store_true")
    options = parser.parse_args()
    output = options.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    config = output / "fontconfig.xml"
    cache = output / "font-cache"
    cache.mkdir(exist_ok=True)
    config.write_text(
        '<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
        f"<dir>{escape(str(ROOT / 'fonts'))}</dir>"
        "<dir>/System/Library/Fonts</dir><dir>/System/Library/Fonts/Supplemental</dir>"
        f"<cachedir>{escape(str(cache))}</cachedir></fontconfig>",
        encoding="utf-8",
    )
    env = {**os.environ, "FONTCONFIG_FILE": str(config)}
    specs = json.loads((ROOT / "sets.json").read_text())
    for index, spec in enumerate(specs, 1):
        if options.only and spec["id"] != options.only:
            continue
        capture = json.loads((options.captures / f"{spec['id']}.json").read_text())
        render(capture, index, output, env, options.poster_only)


if __name__ == "__main__":
    main()
