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
WIDTH, HEIGHT = 1280, 800
SVG_THEME = TerminalTheme(
    (208, 208, 206),
    (43, 43, 41),
    [
        (43, 43, 41),
        (139, 57, 46),
        (49, 93, 55),
        (119, 85, 29),
        (53, 91, 107),
        (99, 69, 99),
        (44, 92, 94),
        (90, 90, 86),
    ],
    [
        (90, 90, 86),
        (139, 57, 46),
        (49, 93, 55),
        (119, 85, 29),
        (53, 91, 107),
        (99, 69, 99),
        (44, 92, 94),
        (43, 43, 41),
    ],
)
SVG_CODE = """<svg xmlns="http://www.w3.org/2000/svg" xml:space="preserve" width="{terminal_width}"
height="{terminal_height}" viewBox="0 0 {terminal_width} {terminal_height}">
<style>.{unique_id}-matrix {{font-family:'JetBrains Mono',monospace;font-size:20px;
font-variant-ligatures:none;white-space:pre;}} {styles}</style>
<defs>{lines}</defs><g class="{unique_id}-matrix" fill="#2b2b29">{backgrounds}{matrix}</g></svg>"""


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


def board(capture, command="", output=""):
    """Only the terminal viewport: a shell prompt followed by captured stdout."""
    values = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}">',
        '<rect width="100%" height="100%" fill="#d0d0ce"/>',
    ]
    prompt, pw, ph = terminal_image("$ " + command, capture["columns"])
    values.append(image_tag(prompt, 24, 20, pw, ph))
    if output:
        svg, sw, sh = terminal_image(output, capture["columns"])
        scale = min(1.0, (WIDTH - 48) / sw, (HEIGHT - max(100, ph + 54)) / sh)
        values.append(image_tag(svg, 24, max(72, ph + 28), sw * scale, sh * scale))
    values.append("</svg>")
    return "".join(values)


def frame(svg, target, env):
    source = target.with_suffix(".svg")
    source.write_text(svg, encoding="utf-8")
    subprocess.run(
        ["rsvg-convert", str(source), "-o", str(target)], env=env, check=True, capture_output=True
    )


def render(capture, output, env, poster_only=False):
    name = capture["id"]
    folder = output / "frames" / name
    folder.mkdir(parents=True, exist_ok=True)
    sequence = []

    def add(svg, seconds, tag):
        png = folder / f"{len(sequence):03}-{tag}.png"
        frame(svg, png, env)
        sequence.append((png, seconds))
        return png

    for stage, recording in enumerate(capture["stages"]):
        command = recording["command"]
        if not poster_only:
            for position in range(0, len(command) + 8, 8):
                add(board(capture, command[:position] + "▌"), 0.055, "type")
        lines = recording["stdout"].strip().splitlines()
        if not poster_only:
            for count in (5, 12):
                add(board(capture, command, "\n".join(lines[:count])), 0.16, "reveal")
        # Camera cuts preserve complete lines. The source capture is retained for auditing.
        first = "\n".join(lines[:34])
        poster = add(board(capture, command, first), 6.0 if stage == 0 else 7.5, "result")
        (output / f"{name}-{'poster' if stage == 0 else 'prep'}.png").write_bytes(
            poster.read_bytes()
        )
        if len(lines) > 34:
            add(board(capture, command, "\n".join(lines[-34:])), 3.0, "continue")
    if poster_only:
        return
    concat = folder / "timeline.txt"
    concat.write_text(
        "".join(f"file '{path.as_posix()}'\nduration {seconds}\n" for path, seconds in sequence)
        + f"file '{sequence[-1][0].as_posix()}'\n",
        encoding="utf-8",
    )
    movie = output / f"gray-demo-{name}.mp4"
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
    for spec in specs:
        if options.only and spec["id"] != options.only:
            continue
        capture = json.loads((options.captures / f"{spec['id']}.json").read_text())
        render(capture, output, env, options.poster_only)


if __name__ == "__main__":
    main()
