#!/usr/bin/env bash
# Start the local AI DP MCP server using the project Conda environment.
# Prerequisites: the root requirements files are installed in codex-4-oci-aidp.
# Usage: scripts/start_aidp_mcp.sh
# Side effect: starts a local stdio MCP process; OCI is contacted only by tools.

set -euo pipefail

exec conda run --no-capture-output -n codex-4-oci-aidp python -m aidp_mcp.server
