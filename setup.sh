#!/bin/sh
# One-command setup: Python environment + the Bionic MCP entry.
# Run it with Bionic quit:  sh setup.sh
set -eu
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
VENV=${DRAWTHINGS_VENV_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/djv-tools-gen/venv}
CONFIG=${BIONIC_MCP_CONFIG:-$HOME/.lmstudio/apps/bionic/.internal/ng-mcp.json}

echo "Draw Things MCP — setup"

# 1. Python environment
if [ ! -x "$VENV/bin/python" ]; then
    echo "→ creating the Python environment (about a minute)…"
    "$DIR/install.sh"
fi
if ! "$VENV/bin/python" -c 'import mcp' >/dev/null 2>&1; then
    echo "→ adding the MCP package…"
    UV=$(command -v uv || echo "$HOME/.local/bin/uv")
    "$UV" pip install -q --python "$VENV/bin/python" 'mcp>=1.2,<2'
fi

# 2. Bionic entry
if [ ! -f "$CONFIG" ]; then
    echo "! No Bionic config found at $CONFIG"
    echo "  Add this object to your MCP client's server list:"
    sed "s|/path/to/DrawThingsMCP|$DIR|" "$DIR/drawthings-mcp-entry.example.json"
    exit 0
fi

case "${1:-}" in
    --force) ;;
    *)
        if ps -Ao comm= | grep -q "Bionic.app/Contents/MacOS/Bionic"; then
            echo "! Bionic is running. Quit it (⌘Q), then run this again."
            echo "  (Or pass --force if you know what you are doing.)"
            exit 1
        fi
        ;;
esac

cp "$CONFIG" "$CONFIG.bak"
"$VENV/bin/python" - "$CONFIG" "$DIR" <<'PY'
import json, sys
path, root = sys.argv[1], sys.argv[2]
with open(path) as f:
    data = json.load(f)
servers = [s for s in data.get("servers", []) if s.get("name") not in ("drawthings", "djv-gen")]
servers.append({
    "id": "0d1f3a64-8b2e-4c71-9a5f-3c7e6d2b1a90",
    "name": "djv-gen",
    "description": "DJV.TOOLS · Gen — Draw Things: local image, edit and short-video generation "
                   "(generate/transform/video/replay/status/guide)",
    "enabled": True,
    "connection": {"type": "stdio", "command": f"{root}/run.sh", "args": [], "env": {}},
})
data["servers"] = servers
with open(path, "w") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write("\n")
print("   servers:", ", ".join(s["name"] for s in servers))
PY

echo
echo "✓ done — previous config saved as ${CONFIG##*/}.bak"
echo "  Next: reopen Bionic and check that Draw Things has"
echo "        Settings → API Server → gRPC + TLS on port 7859."
