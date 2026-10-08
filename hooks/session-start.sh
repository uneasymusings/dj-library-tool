#!/bin/sh
# djlib plugin, at session start: tell the assistant when the djlib engine is missing or from
# another release than this plugin, with the one command that fixes it. Silent when all is well.
installer="curl -LsSf https://raw.githubusercontent.com/uneasymusings/dj-library-tool/main/install.sh | sh"
root="${CLAUDE_PLUGIN_ROOT:-$(dirname "$0")/..}"
plugin=$(sed -n 's/^ *"version": *"\([^"]*\)".*/\1/p' "$root/.claude-plugin/plugin.json" 2>/dev/null | head -n 1)

djlib=$(command -v djlib 2>/dev/null || true)
if [ -z "$djlib" ] && [ -x "$HOME/.local/bin/djlib" ]; then
  djlib="$HOME/.local/bin/djlib"
fi

if [ -z "$djlib" ]; then
  echo "The djlib plugin is installed, but the djlib engine isn't, so the djlib MCP tools can't start."
  echo "If the user wants to use djlib, offer to install it (macOS/Linux): $installer"
  echo "It also sets up FFmpeg and this plugin. Afterwards they reconnect djlib (/mcp) or restart Claude Code."
  exit 0
fi

engine=$("$djlib" --version 2>/dev/null | sed -n 's/^djlib \(.*\)$/\1/p')
if [ -n "$plugin" ] && [ -n "$engine" ] && [ "$plugin" != "$engine" ]; then
  echo "The djlib engine ($engine) and the djlib plugin ($plugin) are from different releases, so tools and instructions may not match."
  echo "If djlib comes up, offer to run \`djlib upgrade\` (it updates both), then restart Claude Code."
fi
exit 0
