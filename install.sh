#!/bin/sh
# djlib installer: curl -LsSf https://raw.githubusercontent.com/uneasymusings/dj-library-tool/main/install.sh | sh
# Installs uv if needed, the newest djlib release, FFmpeg through Homebrew when available and
# the djlib plugin for Claude Code and Codex when they are installed. In a terminal it then
# offers to index your music. Safe to run again: it updates whatever is already there.
#   DJLIB_VERSION=0.1.0a16  pin a release
#   DJLIB_PLUGIN=0          don't add the Claude Code / Codex plugin
#   DJLIB_MUSIC=~/Music     index this folder without asking (DJLIB_MUSIC= skips it)
set -eu

repo="uneasymusings/dj-library-tool"
say() { printf '\033[1m%s\033[0m\n' "$*"; }
note() { printf '  %s\n' "$*"; }

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
# Python 3.12: song recognition (shazamio) has macOS and Windows wheels there, not on 3.13.
uv tool install --force --compile-bytecode --python 3.12 "dj-library-tool[download,recognize] @ $wheel"
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

# The plugin gives Claude Code and Codex djlib's tools and the skill that teaches the workflow.
plugins=""
if [ "${DJLIB_PLUGIN:-1}" != 0 ]; then
  if command -v claude >/dev/null 2>&1; then
    say "Adding the djlib plugin to Claude Code…"
    if claude plugin marketplace list </dev/null 2>/dev/null | grep -q "dj-library-tool"; then
      claude plugin marketplace update dj-library-tool </dev/null >/dev/null 2>&1 || true
    else
      claude plugin marketplace add "$repo" </dev/null >/dev/null 2>&1 || true
    fi
    if claude plugin list </dev/null 2>/dev/null | grep -q "djlib@dj-library-tool"; then
      claude plugin update djlib@dj-library-tool </dev/null >/dev/null 2>&1 || true
    else
      claude plugin install djlib@dj-library-tool </dev/null >/dev/null 2>&1 || true
    fi
    if claude plugin list </dev/null 2>/dev/null | grep -q "djlib@dj-library-tool"; then
      plugins=" Claude Code"
    else
      note "Couldn't add it; run: claude plugin marketplace add $repo && claude plugin install djlib@dj-library-tool"
    fi
  fi
  if command -v codex >/dev/null 2>&1; then
    say "Adding the djlib plugin to Codex…"
    if codex plugin marketplace list </dev/null 2>/dev/null | grep -q "dj-library-tool"; then
      codex plugin marketplace upgrade </dev/null >/dev/null 2>&1 || true
    else
      codex plugin marketplace add "$repo" </dev/null >/dev/null 2>&1 || true
    fi
    codex plugin add djlib@dj-library-tool </dev/null >/dev/null 2>&1 || true
    if codex plugin list </dev/null 2>/dev/null | grep -q "djlib@dj-library-tool"; then
      plugins="$plugins${plugins:+ and} Codex"
    else
      note "Couldn't add it; run: codex plugin marketplace add $repo && codex plugin add djlib@dj-library-tool"
    fi
  fi
fi

# A folder dragged into Terminal arrives as /Volumes/DJ\ Drive/Music with a trailing space,
# and a copied path may be quoted: trim, unquote and unescape it, then expand ~.
folder_answer() {
  answer=$(printf '%s' "$1" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' \
    -e "s/^'\(.*\)'\$/\1/" -e 's/^"\(.*\)"$/\1/' -e 's/\\\(.\)/\1/g')
  case "$answer" in
    "~") printf '%s\n' "$HOME" ;;
    "~/"*) printf '%s\n' "$HOME/${answer#\~/}" ;;
    *) printf '%s\n' "$answer" ;;
  esac
}

# Index the user's music once: only when nothing is set up yet, and only after asking (or
# with DJLIB_MUSIC). Assistants running this skip it and ask the user themselves.
unset_up() { "$djlib" status 2>/dev/null | grep -q '"WORKSPACE_REQUIRED"'; }
music=""
if unset_up; then
  if [ -n "${DJLIB_MUSIC+set}" ]; then
    music="${DJLIB_MUSIC}"
  elif [ -t 1 ] && (: </dev/tty) 2>/dev/null; then
    printf '\033[1mWhere is your music?\033[0m Drag the folder here, Enter for ~/Music, or type skip: '
    read -r answer </dev/tty || answer="skip"
    music=$(folder_answer "$answer")
    case "$music" in
      "") music="$HOME/Music" ;;
      skip | Skip | SKIP) music="" ;;
    esac
  fi
fi
if [ -n "$music" ]; then
  if [ -d "$music" ]; then
    say "Indexing $music in place (nothing is moved or retagged; about 4 minutes per 1,000 tracks)…"
    "$djlib" init --allow-root "$music" >/dev/null && "$djlib" scan || true
  else
    printf '%s isn'\''t a folder. Later: djlib init "%s" && djlib scan\n' "$music" "$music" >&2
  fi
fi

say "Done. Checking your setup:"
"$djlib" doctor || true
echo
if [ -n "$plugins" ]; then
  say "djlib is in$plugins: restart it, then ask for example:"
  note "\"What's in this set and which tracks do I own? https://soundcloud.com/…\""
fi
if unset_up; then
  say "First tell djlib where your music is:"
  note "djlib init ~/Music && djlib scan"
  say "Then try one of these:"
else
  say "Try one of these:"
fi
note "djlib set 'https://youtu.be/…'   every track in a set, and which you own"
note "djlib shazam status              your Shazams, and how to check them"
note "djlib set paste                  a tracklist you copied"
note "Add --usb to finish on your USB stick. Put links in single quotes."
if [ "$(uname -s)" = "Darwin" ]; then
  echo "rekordbox and USB steps need your terminal (and Claude Code or Codex) allowed under"
  echo "System Settings → Privacy & Security → Accessibility; djlib doctor shows whether it is."
fi
echo "If 'djlib' isn't found, open a new terminal window."
