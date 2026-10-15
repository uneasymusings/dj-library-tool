#!/bin/sh
# djlib plugin, at session start: tell the assistant when the djlib engine is missing, can't be
# started by Claude Code (installed, but its folder isn't on PATH) or is from another release
# than this plugin, with the one command that fixes it. Silent when all is well.
installer="curl -LsSf https://raw.githubusercontent.com/uneasymusings/dj-library-tool/main/install.sh | sh"
root="${CLAUDE_PLUGIN_ROOT:-$(dirname "$0")/..}"
plugin=$(sed -n 's/^ *"version": *"\([^"]*\)".*/\1/p' "$root/.claude-plugin/plugin.json" 2>/dev/null | head -n 1)

djlib=$(command -v djlib 2>/dev/null || true)
upgrade="djlib upgrade"
if [ -z "$djlib" ]; then
  # Where uv tool install (and so the installer) puts djlib. The plugin starts its MCP server
  # as plain `djlib`, which fails when that folder isn't on Claude Code's PATH.
  for folder in "${UV_TOOL_BIN_DIR:-}" "${XDG_BIN_HOME:-}" "$HOME/.local/bin"; do
    if [ -n "$folder" ] && [ -x "$folder/djlib" ]; then
      djlib="$folder/djlib"
      break
    fi
  done
  if [ -n "$djlib" ]; then
    shown="$folder" exported="$folder"
    case "$folder" in "$HOME"/*) shown="~${folder#"$HOME"}" exported="\$HOME${folder#"$HOME"}" ;; esac
    if command -v uv >/dev/null 2>&1; then
      fix="run \`uv tool update-shell\`"
    elif [ -x "$folder/uv" ]; then
      fix="run \`$shown/uv tool update-shell\`"
    else
      fix="add \`export PATH=\"$exported:\$PATH\"\` to their shell profile"
    fi
    upgrade="$shown/djlib upgrade"
    echo "djlib is in $shown, which isn't on Claude Code's PATH, so the djlib MCP tools can't start. Offer to $fix, then restart Claude Code from a new terminal window."
  fi
fi

if [ -z "$djlib" ]; then
  echo "The djlib plugin is installed, but the djlib engine isn't, so the djlib MCP tools can't start."
  echo "If the user wants to use djlib, offer to install it (macOS/Linux): $installer"
  echo "It also sets up FFmpeg and this plugin. Afterwards they reconnect djlib (/mcp) or restart Claude Code."
  exit 0
fi

# Python on Windows ends the line with \r\n.
engine=$("$djlib" --version 2>/dev/null | tr -d '\r' | sed -n 's/^djlib \(.*\)$/\1/p')
if [ -n "$plugin" ] && [ -n "$engine" ] && [ "$plugin" != "$engine" ]; then
  echo "The djlib engine ($engine) and the djlib plugin ($plugin) are from different releases, so tools and instructions may not match."
  echo "If djlib comes up, offer to run \`$upgrade\` (it updates both), then restart Claude Code."
fi
exit 0
