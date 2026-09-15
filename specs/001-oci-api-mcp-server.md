# OCI API MCP Server evaluation

## Problem and scope

Evaluate installation of Oracle's OCI API MCP Server in the existing
`codex-4-oci-aidp` Conda environment and register it with the local Codex
client. The server is an OCI CLI wrapper; it is not a dedicated AI Data
Platform API client.

## Non-goals

This work does not invoke OCI, create or modify cloud resources, change IAM
policies, or replace the project's AI Data Platform SDK scripts.

## Assumptions and prerequisites

The target Conda environment already exists. The server must support its Python
version, and its OCI CLI dependency must use the user's existing, least-
privilege OCI profile without storing credentials in this repository or Codex
configuration.

## Intended behavior

Codex starts the server over stdio from the target environment. On a new Codex
session, the server makes Oracle's discovery and OCI CLI execution tools
available. A repository launcher reads only `OCI_CONFIG_FILE` and `OCI_PROFILE`
from the ignored root `.env`, passing them to the server as `OCI_CONFIG_FILE`
and `OCI_CONFIG_PROFILE`; it selects API-key authentication. Credentials and
the AI DP instance ID remain outside the Codex global configuration.

## Acceptance criteria

* A released server version compatible with the target Python is installed.
* The server executable and OCI CLI executable resolve inside the Conda
  environment.
* `codex mcp list` reports the registered stdio server.
* Installation checks do not invoke OCI APIs.
* The MCP process resolves the same OCI config file and profile as the project
  without logging their values or copying credentials.
* Versions, limitations, verification results, and recovery procedure are
  recorded here.

## Verification approach

Inspect the installed package metadata and executable help, then register the
server with `codex mcp add` and inspect `codex mcp list`. Do not call an MCP
tool in this running session: a new session is required to load a newly
configured server.

## Sources

Verified 2026-09-15:

* <https://github.com/oracle/mcp/tree/main/src/oci-api-mcp-server>
* <https://www.oracle.com/mcp/>

## Results

2026-09-15: Installation in `codex-4-oci-aidp` was not possible. The
environment uses Python 3.11.0, while pip reported that every released version
of `oracle.oci-api-mcp-server` (1.0.0 through 2.1.5) requires Python 3.13 or
newer. No package or dependency was installed and no OCI API was invoked.

The compatible recovery path is a separate Python 3.13 Conda environment for
the MCP server. It must not replace the project's Python 3.11 environment,
whose pinned AI Data Platform SDK has been verified there. The user authorized
creation of `codex-mcp-ai-dp`; install the server there and configure Codex to
launch its `oracle.oci-api-mcp-server` executable through `conda run`.

2026-09-15: Created the separate `codex-mcp-ai-dp` Conda environment with
Python 3.13.15. Installed `oracle.oci-api-mcp-server==2.1.5`, which installed
`oci-cli==3.89.3`; the CLI reported version `3.89.3`. Codex now has an enabled
global stdio registration named `oci-api` with this launch command:

```text
conda run --no-capture-output -n codex-mcp-ai-dp oracle.oci-api-mcp-server
```

`FASTMCP_LOG_LEVEL=ERROR` is set only to reduce server log noise. The
registration contains no OCI credential, profile, endpoint, or compartment
value. `codex mcp list` confirmed the registration. Its authentication status
is reported as `Unsupported`, as expected for a local stdio server; OCI CLI
authentication is resolved at server runtime from the user's local profile.

The server was launched locally and the bundled OCI CLI version was checked.
No MCP tool call and no OCI API request was made. This Codex session cannot
load a server added after the session started; open a new Codex session to make
the tools callable.

2026-09-15: The installed OCI CLI exposes the `ai-data-platform` control-plane
command group. A launcher has been added at
`scripts/start_oci_api_mcp.sh` to apply the repository's OCI config-file and
profile selection without sourcing `.env` or copying secret values. It cannot
make the server data-plane-aware: `AIDP_INSTANCE_ID` and `AIDP_ENDPOINT` belong
to the generated AI DP SDK used by this repository, not to OCI CLI commands.
For control-plane inspection, provide the AI DP instance ID from `.env` as the
argument of the relevant OCI CLI command; do not paste credentials into a
prompt.

2026-09-15: The `oci-api` Codex registration was updated to start the project
launcher instead of invoking Conda directly. `codex mcp list` confirmed the
enabled stdio registration. Shell syntax validation passed and OCI CLI help
confirmed the read-only inspection syntax:

```text
ai-data-platform ai-data-platform get --ai-data-platform-id <instance-ocid>
```

This command was not run against OCI. In a new Codex session, use
`get_oci_command_help` first and then `run_oci_command` only after reviewing
the generated command. The latter can execute mutations and must not be used
for an inspection-only experiment.

2026-09-15: At the user's request, removed the global `oci-api` MCP
registration. `codex mcp list` confirmed that no MCP servers remain
configured. The isolated Conda environment and project launcher remain in
place for possible future evaluation; neither is a running service.

## Recovery

Remove the Codex registration with `codex mcp remove oci-api`. Uninstall the
recorded package from `codex-mcp-ai-dp` only if no other local work uses it.
