#!/usr/bin/env bash
#
# Start the Oracle OCI API MCP Server with the OCI profile selected by this
# repository's .env file.
#
# Prerequisites:
#   * Conda environment: codex-mcp-ai-dp
#   * oracle.oci-api-mcp-server installed in that environment
#   * Root .env with OCI_CONFIG_FILE and OCI_PROFILE, or a valid OCI default
#
# Inputs:
#   AIDP_ENV_FILE optionally selects an alternative dotenv file. The file is
#   parsed as data; it is never sourced as shell code.
#
# Side effects:
#   Starts a local stdio MCP process. OCI is contacted only if an MCP tool runs
#   an OCI CLI command.
#
# Usage:
#   scripts/start_oci_api_mcp.sh
#
# Supported shell: Bash 3.2 or later.

set -euo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
project_dir="$(CDPATH= cd -- "$script_dir/.." && pwd)"
env_file="${AIDP_ENV_FILE:-$project_dir/.env}"

dotenv_value() {
    local key="$1"
    awk -v key="$key" '
        $0 ~ "^[[:space:]]*" key "[[:space:]]*=" {
            sub("^[[:space:]]*" key "[[:space:]]*=", "")
            sub("[[:space:]]*(#.*)?$", "")
            print
            exit
        }
    ' "$env_file"
}

if [[ ! -r "$env_file" ]]; then
    printf '%s\n' "Cannot read environment file: $env_file" >&2
    exit 2
fi

oci_config_file="$(dotenv_value OCI_CONFIG_FILE)"
oci_profile="$(dotenv_value OCI_PROFILE)"

export OCI_CONFIG_FILE="${oci_config_file:-$HOME/.oci/config}"
export OCI_CONFIG_PROFILE="${oci_profile:-DEFAULT}"
export OCI_MCP_AUTH_TYPE="api_key"

exec conda run --no-capture-output -n codex-mcp-ai-dp \
    oracle.oci-api-mcp-server
