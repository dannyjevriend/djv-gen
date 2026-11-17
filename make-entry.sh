#!/bin/sh
# Print the Bionic MCP entry for THIS folder — paste the output into ng-mcp.json.
set -eu
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cat <<JSON
{
  "id": "0d1f3a64-8b2e-4c71-9a5f-3c7e6d2b1a90",
  "name": "djv-gen",
  "description": "DJV.TOOLS · Gen — Draw Things: local image, edit and short-video generation (generate/transform/video/replay/status/guide)",
  "enabled": true,
  "connection": {
    "type": "stdio",
    "command": "$DIR/run.sh",
    "args": [],
    "env": {}
  }
}
JSON
