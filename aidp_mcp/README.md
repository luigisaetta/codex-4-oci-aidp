# AI DP MCP server

`aidp-mcp` is a local [Model Context Protocol (MCP)] server for selected
Oracle Cloud Infrastructure (OCI) AI Data Platform (AI DP) workflows. It lets
an MCP client discover AI DP workspace, workflow, cluster, catalog, and volume
metadata, and perform a deliberately limited set of explicitly confirmed
operations.

The server supports the **stdio** transport only. It was designed to be
started as a local child process by code assistants such as Codex or Claude
Code: MCP messages are exchanged through standard input and output, and the
server does not expose an HTTP listener or a network port. OCI is contacted
only when a tool requires it.

## Prerequisites

Run the setup in the repository root first:

* The `codex-4-oci-aidp` Conda environment exists and contains the project
  dependencies.
* Either the root `.env` is present, based on [`.env.example`](../.env.example),
  or `AIDP_ENV_FILE` names an absolute external settings file. Both contain
  the non-secret connection settings required by `aidp_common`.
* The selected OCI profile and private key are available locally and the
  authenticated identity has the AI DP permissions required for the requested
  action.

The launcher is [`scripts/start_aidp_mcp.sh`](../scripts/start_aidp_mcp.sh).
It starts `python -m aidp_mcp.server` in the project Conda environment and can
be launched from any working directory. Do not start it manually in an
interactive terminal: an MCP client must own its standard input and output.

For the lifetime of its process, the server caches only the resolved AI DP
instance OCID and workspace key; SDK clients, credentials, and tool results
remain per-request. A changed applicable `.env` target setting is resolved on
the next request (process environment variables still take precedence). A
deleted or renamed target can fail once, clears its cached target on HTTP 404,
and is resolved again by the following request. Prefer a compartment OCID over
a name to avoid the initial OCI Identity lookup.

### Settings-file selection

The server reads the repository `.env` by default. Set `AIDP_ENV_FILE` in the
server process environment to select a different, absolute settings-file path;
the value cannot be defined inside dotenv. This lets one installed server use
separate settings for distinct AI DP environments. For command-line features,
`--env-file` takes precedence over `AIDP_ENV_FILE`; for the MCP server, the
precedence is `AIDP_ENV_FILE` then the repository default. A missing or
relative `AIDP_ENV_FILE` is rejected without exposing its path.

### Local upload roots

`AIDP_ALLOWED_ROOTS` optionally lists local directories from which notebooks
may be uploaded, separated by the operating system path separator (`:` on
macOS and Linux). An empty or absent value permits only this repository. Each
configured directory must exist and cannot be the filesystem root, the user's
home directory, or an ancestor of the home directory. These limits prevent an
MCP tool from reading broadly scoped local configuration or key material.

The setting is operator configuration in `.env` or the process environment,
not an `upload_notebook` parameter. A model therefore cannot expand its own
local-file access. Symbolic links are resolved before the containment check.
When two or more roots are configured, provide an absolute `local_path` to
`upload_notebook`; relative paths are rejected so they cannot resolve against
the server repository's working directory.

### Code layout

`service.py` is the small facade used by `server.py`. Notebook, workflow-job,
cluster, and catalog-volume operations are selected through `notebooks.py`,
`jobs.py`, `clusters.py`, and `volumes.py`; `safety.py` centralizes explicit
mutation confirmation. New domains, such as future AI DP agents, belong in a
new domain module rather than in the facade. Dependencies flow from server to
the facade, domain modules, shared helpers, and finally `aidp_common`; domain
modules must not depend on one another.

## Tools

| Tool | Type | Description |
| --- | --- | --- |
| `upload_notebook` | Mutation, plan by default | Validates an allowed-root local `.ipynb` file and plans or uploads it to an explicit workspace-relative path. Set `apply=true` to mutate; replacing an existing notebook also requires `overwrite=true`. |
| `list_notebooks` | Read-only | Lists bounded, non-recursive notebook metadata in an explicit `/Workspace` directory, with an optional name substring filter. |
| `find_notebook_jobs` | Read-only | Finds workflow jobs in the configured workspace that use one exact `/Workspace/...ipynb` notebook. |
| `list_catalog_volumes` | Read-only | Lists visible schemas and volumes in one exact catalog. By default, only external Object Storage volumes are returned. |
| `list_volume_files` | Read-only | Returns a bounded recursive tree of folders and files below a path in one exact catalog, schema, and volume; it never reads file content. |
| `list_job_runs` | Read-only | Lists bounded, newest-first run summaries for exactly one job selected by name or key. |
| `ensure_notebook_job` | Mutation, plan by default | Plans or reconciles a managed single-notebook workflow job for an exact notebook path and cluster. Set `apply=true` to create or update it. |
| `start_notebook_job` | Mutation | Starts a managed notebook job. Requires `confirm_start=true`; optional waiting is bounded by `timeout_seconds`. |
| `get_job_run` | Read-only | Retrieves sanitized status for a job run previously submitted to AI DP. |
| `get_cluster_status` | Read-only | Retrieves the state and a small sanitized configuration summary for an exact cluster. |
| `set_cluster_state` | Mutation | Starts or stops an exact cluster. Requires `confirm_action=true`; starting may incur compute charges and stopping can interrupt work. |
| `get_job_run_output` | Read-only | Retrieves bounded plain-text output for a managed single-notebook job run. Output can contain application data, so request it only when authorized. |

All collection and output tools enforce local bounds. The server uses exact
resource matching where applicable and returns sanitized metadata rather than
credentials, notebook content, volume-file content, or arbitrary SDK payloads.

## Use with Codex

Preferred: install the editable package and register its command once with the
Codex CLI. Install dependencies first, then run:

```bash
conda activate codex-4-oci-aidp
python -m pip install --no-deps --no-build-isolation -e .
conda run -n codex-4-oci-aidp python -c "import shutil; print(shutil.which('aidp-mcp'))"
```

The preferred registration replaces any existing `aidp-mcp` entry. Remove it
first, then copy the printed absolute executable path into one of these
registrations:

```bash
codex mcp remove aidp-mcp

# Use the repository default .env.
codex mcp add aidp-mcp -- /absolute/path/to/aidp-mcp

# Or select an external settings file for this server.
codex mcp add aidp-mcp \
  --env AIDP_ENV_FILE=/absolute/path/to/aidp.env \
  -- /absolute/path/to/aidp-mcp
```

The equivalent `~/.codex/config.toml` configuration is:

```toml
[mcp_servers.aidp-mcp]
command = "/absolute/path/to/aidp-mcp"

[mcp_servers.aidp-mcp.env]
AIDP_ENV_FILE = "/absolute/path/to/aidp.env"
```

Without the Codex CLI (for example, when using only the Codex IDE extension),
edit `~/.codex/config.toml` directly using this form. Replace the `command` of
an existing `aidp-mcp` entry instead of adding a second entry, then start a
new Codex session so the change is loaded.

The command stores the executable path and optional settings-file path in
Codex configuration; it does not store OCI credentials, private keys, OCIDs,
or endpoints. The `--env` CLI syntax and `env` table were verified on
2026-09-27 against [official OpenAI Codex MCP documentation](https://developers.openai.com/codex/mcp/).
Confirm the registration:

```bash
codex mcp list
```

Start a new Codex session after adding or changing the registration. In the
Codex TUI, use `/mcp` to inspect active MCP servers. Then ask Codex for a
concrete, scoped action, for example: “List external volumes in catalog
`<catalog-name>`” or “Plan the upload of `notebooks/example.ipynb` to
`examples/example.ipynb`."

The existing launcher remains supported and passes a caller-provided
`AIDP_ENV_FILE` through unchanged:

```bash
codex mcp add aidp-mcp-launcher -- "$(pwd)/scripts/start_aidp_mcp.sh"
```

Only one registration should be active: use either the preferred installed
`aidp-mcp` entry or `aidp-mcp-launcher`, not both. Otherwise Codex sees the
same tools twice. After registration, either launch method can be used from
any Codex project.

To replace the registration after moving the repository, remove it and add it
again from the new repository root:

```bash
codex mcp remove aidp-mcp
codex mcp add aidp-mcp -- "$(pwd)/scripts/start_aidp_mcp.sh"
```

Codex supports local stdio MCP servers and shares their configuration among
the Codex CLI, IDE extension, and ChatGPT desktop app on the same host. See
the [official OpenAI MCP documentation](https://developers.openai.com/codex/mcp/)
for alternative configuration through `config.toml`, startup options, and tool
approval policies.

## Safety and verification

Prefer read-only discovery and planning calls before remote changes. Confirm
the resolved target and intent before setting `apply`, `confirm_start`, or
`confirm_action` to `true`. A local test suite does not verify AI DP runtime
compatibility; live OCI verification remains an explicit, authorized step.

For design and acceptance details, see
[the MCP workflow specification](../specs/002-notebook-workspace-job-mcp.md)
and [the external-volume specification](../specs/004-external-volume-exploration-mcp.md).
