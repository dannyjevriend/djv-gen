#!/bin/sh
# Launch the Draw Things MCP server (stdio). Uses the shared venv and adds the
# `mcp` package to it on first run.
set -eu

# Keep the package clean: no .pyc files, and renders go to a stable folder
# outside the package unless the caller passes `out`.
export PYTHONDONTWRITEBYTECODE=1
export DRAWTHINGS_OUTPUT_DIR=${DRAWTHINGS_OUTPUT_DIR:-$HOME/Pictures/drawthings}

DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
VENV=${DRAWTHINGS_VENV_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/djv-tools-gen/venv}

[ -f "$DIR/gen.py" ] || { echo '{"ok": false, "error": "gen.py is missing from the package"}' >&2; exit 1; }
[ -x "$VENV/bin/python" ] || "$DIR/install.sh" >&2

if ! "$VENV/bin/python" -c 'from mcp.server.fastmcp import FastMCP' >/dev/null 2>&1; then
    UV=$(command -v uv || echo "$HOME/.local/bin/uv")
    "$UV" pip install --quiet --python "$VENV/bin/python" 'mcp>=1.2,<2' >&2
fi

exec "$VENV/bin/python" "$DIR/server.py" "$@"
