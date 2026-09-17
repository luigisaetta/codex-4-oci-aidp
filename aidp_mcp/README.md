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
* The root `.env` is present, based on [`.env.example`](../.env.example), and
  contains the non-secret connection settings required by `aidp_common`.
* The selected OCI profile and private key are available locally and the
  authenticated identity has the AI DP permissions required for the requested
  action.

The launcher is [`scripts/start_aidp_mcp.sh`](../scripts/start_aidp_mcp.sh).
It starts `python -m aidp_mcp.server` in the project Conda environment. Do not
start it manually in an interactive terminal: an MCP client must own its
standard input and output.

## Tools

| Tool | Type | Description |
| --- | --- | --- |
| `upload_notebook` | Mutation, plan by default | Validates a repository-local `.ipynb` file and plans or uploads it to an explicit workspace-relative path. Set `apply=true` to mutate; replacing an existing notebook also requires `overwrite=true`. |
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

From the repository root, register the local stdio server with the Codex CLI:

```bash
codex mcp add aidp-mcp -- "$(pwd)/scripts/start_aidp_mcp.sh"
```

The command stores the launcher path in Codex configuration; it does not store
OCI credentials, private keys, OCIDs, endpoints, or `.env` values. Confirm the
registration:

```bash
codex mcp list
```

Start a new Codex session after adding or changing the registration. In the
Codex TUI, use `/mcp` to inspect active MCP servers. Then ask Codex for a
concrete, scoped action, for example: “List external volumes in catalog
`<catalog-name>`” or “Plan the upload of `notebooks/example.ipynb` to
`examples/example.ipynb`.”

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
