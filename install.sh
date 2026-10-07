#!/bin/sh
# djlib installer: curl -LsSf https://raw.githubusercontent.com/uneasymusings/dj-library-tool/main/install.sh | sh
# Installs uv if needed, the newest djlib release, FFmpeg through Homebrew when available,
# then runs `djlib doctor`. Set DJLIB_VERSION=0.1.0a13 to pin a release.
set -eu

repo="uneasymusings/dj-library-tool"
say() { printf '\033[1m%s\033[0m\n' "$*"; }

if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv (Python tool manager)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  PATH="$HOME/.local/bin:$PATH"
  export PATH
fi

if [ -n "${DJLIB_VERSION:-}" ]; then
  wheel="https://github.com/$repo/releases/download/v$DJLIB_VERSION/dj_library_tool-$DJLIB_VERSION-py3-none-any.whl"
else
  # Newest published release, pre-releases included (no jq needed).
  wheel=$(curl -fsSL "https://api.github.com/repos/$repo/releases?per_page=5" \
    | grep -o '"browser_download_url": *"[^"]*-py3-none-any\.whl"' \
    | head -n 1 | sed 's/.*"\(https[^"]*\)"$/\1/')
fi
if [ -z "$wheel" ]; then
  echo "Couldn't find a djlib release on GitHub. Try again, or see https://github.com/$repo" >&2
  exit 1
fi

say "Installing djlib from $wheel"
uv tool install --force --compile-bytecode --python 3.13 "dj-library-tool[download] @ $wheel"
uv tool update-shell >/dev/null 2>&1 || true

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    say "Installing FFmpeg with Homebrew (needed for MP3, FLAC, AIFF and M4A)…"
    brew install ffmpeg
  else
    echo "djlib needs FFmpeg for MP3/FLAC/AIFF/M4A: https://ffmpeg.org/download.html" >&2
  fi
fi

djlib="$(command -v djlib || echo "$HOME/.local/bin/djlib")"
say "Done. Checking your setup:"
"$djlib" doctor || true
echo
echo "Next: djlib init --allow-root ~/Music   then   djlib scan   then   djlib set tracklist.txt"
echo "If 'djlib' isn't found, open a new terminal window."
