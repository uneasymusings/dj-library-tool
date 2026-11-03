"""Join actual browser captures into a short, silent UI walkthrough with simple cuts.

The input JPEGs are unaltered screenshots of the public demo's production frontend.
This is a paced sequence of captured states, not continuous device automation footage.
"""

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    movie = output / "gray-browser-walkthrough.mp4"
    gif = output / "review-page-gray.gif"
    stages = [("set", 4), ("rip", 4), ("crate", 5)]
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)
        lines = []
        for index, (name, seconds) in enumerate(stages):
            shutil.copyfile(
                ROOT / f"docs/images/review-page-gray-{name}.jpg", folder / f"{index}.jpg"
            )
            lines.append(f"file '{index}.jpg'\nduration {seconds}\n")
        (folder / "timeline.txt").write_text("".join(lines) + "file '2.jpg'\n", encoding="utf-8")
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
                "timeline.txt",
                "-t",
                "13",
                "-vf",
                "pad=ceil(iw/2)*2:ceil(ih/2)*2:color=0xd0d0ce,fps=15,format=yuv420p",
                "-c:v",
                "libx264",
                "-crf",
                "18",
                "-preset",
                "fast",
                "-movflags",
                "+faststart",
                str(movie),
            ],
            cwd=folder,
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
                "fps=2,scale=1080:-2:flags=lanczos,palettegen=max_colors=128",
                str(palette),
            ],
            check=True,
        )
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
                "fps=2,scale=1080:-2:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=4",
                "-loop",
                "0",
                str(gif),
            ],
            check=True,
        )
    for path in (movie, gif):
        subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"],
            capture_output=True,
            check=True,
        )
    print("Browser walkthrough: 13 seconds, three real captured states; both files fully decode.")


if __name__ == "__main__":
    main()
