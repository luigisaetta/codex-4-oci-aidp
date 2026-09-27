#!/usr/bin/env bash
# Start the local AI DP MCP server from any caller working directory.
# Prerequisites: the root requirements files are installed in codex-4-oci-aidp.
# Usage: scripts/start_aidp_mcp.sh
# Side effect: starts a local stdio MCP process; OCI is contacted only by tools.

set -euo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd -- "$script_dir/.."

exec conda run --no-capture-output -n codex-4-oci-aidp python -m aidp_mcp.server
