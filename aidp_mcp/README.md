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
and agent-code folders may be uploaded, separated by the operating system path
separator (`:` on macOS and Linux). An empty or absent value permits only this
repository. Each configured directory must exist and cannot be the filesystem
root, the user's home directory, or an ancestor of the home directory. These
limits prevent an MCP tool from reading broadly scoped local configuration or
key material.

The setting is operator configuration in `.env` or the process environment,
not an upload-tool parameter. A model therefore cannot expand its own
local-file access. Symbolic links are resolved before the containment check.
Provide an absolute local path whenever an `AIDP_ALLOWED_ROOTS` directory is
configured. Relative paths are accepted only with the default repository root,
so an explicitly configured root cannot silently resolve a server-repository
file.

### Code layout

`server.py` is the MCP adapter: it loads validated settings for each tool
request and calls the Python domain API directly. `agents.py`, `agent_code.py`,
`agent_deploy.py`, `agent_invoke.py`, `operations_status.py`, `notebooks.py`,
`jobs.py`, `clusters.py`, and `volumes.py` are domain modules; each receives
validated settings explicitly. `agents.py` performs read-only agent
observation, `agent_code.py` performs guarded code upload and CODE-definition
reconciliation, `agent_deploy.py` plans and submits guarded deployments, and
`agent_invoke.py` performs confirmation-gated OCI-signed deployment
invocation. `operations_status.py` provides bounded, sanitized operation
status observation. `agent_lookup.py` contains the agent modules' shared
exact-name lookup, deployment listing, and response shaping helpers.
`workspace_files.py` supplies shared workspace object reads, file uploads, and
folder creation to agents and notebooks. `config.py`, `local_files.py`,
`targets.py`, `lookups.py`, `validation.py`, and `safety.py` are shared
modules, with `safety.py` centralizing explicit mutation confirmation.
Dependencies flow from `server.py` to domain modules, then shared modules, and
finally `aidp_common`; domain modules must not depend on one another.

## Tools

| Tool | Type | Description |
| --- | --- | --- |
| `upload_notebook` | Mutation, plan by default | Validates an allowed-root local `.ipynb` file and plans or uploads it to an explicit workspace-relative path. Set `apply=true` to mutate; replacing an existing notebook also requires `overwrite=true`. |
| `list_notebooks` | Read-only | Lists bounded, non-recursive notebook metadata in an explicit `/Workspace` directory, with an optional name substring filter. |
| `list_aidp_agents` | Read-only | Lists bounded metadata for agents in the configured workspace. |
| `get_aidp_agent` | Read-only | Retrieves one exact-name agent and bounded deployment metadata. |
| `list_aidp_async_operations` | Read-only | Lists bounded, newest-first sanitized async operations for `AGENT`, `AI_COMPUTE`, or `CLUSTER`. It supports a status and display-name filter, and includes bounded failure details without exposing OCIDs or creator identifiers. |
| `list_aidp_agent_sessions` | Read-only | Lists bounded, newest-first sessions for one exact-name agent. |
| `get_aidp_agent_session_messages` | Read-only | Retrieves bounded session messages for an exact-name agent; messages can contain application data. |
| `get_aidp_agent_trace` | Read-only | Retrieves bounded, sanitized trace spans for an exact-name agent session. |
| `upload_aidp_agent_code` | Mutation, plan by default | Safely compares or uploads an allowed-root local agent folder to an absolute `/Workspace/...` directory. Set `apply=true` to write; replacing changed files also requires `overwrite=true`. Secret-like files and symbolic links are refused, and remote-only files are reported but never deleted. |
| `ensure_aidp_agent` | Mutation, plan by default | Plans or creates/minimally updates an exact-name CODE agent definition whose entry and optional dependency file already exist in the workspace. Set `apply=true` to write. It never attaches compute, deploys, redeploys, or changes guardrails, sessions, or cards. |
| `deploy_aidp_agent` | Mutation, plan by default | Plans a deploy or redeploy of an exact CODE agent on an already ACTIVE AI Compute. Set `apply=true` only after approval; it submits one no-retry request and can wait for the PROD deployment. Redeploy recreates the deployment, may briefly make the endpoint unavailable, and is required after every code upload. Nothing is deleted. |
| `invoke_aidp_agent` | Confirmation-gated | Sends one bounded message to the exact-name agent's sole ACTIVE deployment. Requires `confirm_invoke=true`; it creates a session and can consume compute or trigger agent-tool side effects. |
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

### Agent code workflow

Use the two plan-by-default agent tools in this order. First run
`upload_aidp_agent_code` with the approved local folder and an explicit
`/Workspace/...` destination. Review its content-hash plan, including any
`remote_only` files; use `apply=true` only when authorized, and use
`overwrite=true` only after reviewing changed remote files. Then run
`ensure_aidp_agent` with paths relative to that workspace folder, such as
`entry_file="hello_agent.py"` and `dependencies_file="requirements.txt"`.
It verifies those remote files before producing its plan. Applying the plan
creates or updates only the three CODE-definition fields it reports.

Neither tool deploys an agent or attaches AI Compute. A deployed agent's
definition changes require the separate, authorized deployment workflow below
before they take effect. The tools do not delete workspace files, folders,
agents, or deployments. See [specification 012](../specs/012-agent-code-upload-and-draft-mcp.md)
for code-upload acceptance criteria, live-verification prerequisites, and
cleanup steps.

### Agent deployment workflow

After the upload and definition workflow, call `get_cluster_status` for the
selected AI Compute. It must be an ACTIVE AI Compute before deployment; if it
is not active, explain the cost and obtain separate approval before using
`set_cluster_state` to start it.

Call `deploy_aidp_agent` with `apply=false` and review its exact agent,
compute, action, and existing PROD deployment summary. It accepts only a CODE
agent whose configured workspace files exist and either creates the first PROD
deployment or redeploys one sole ACTIVE PROD deployment. Ambiguous, failed,
creating, or inactive deployment states stop with an error rather than a
guess. After explicit current-conversation approval, call it with `apply=true`.
The request is never retried automatically; the default bounded wait verifies
an ACTIVE PROD deployment, and a redeploy also verifies a newer creation time.

Every code upload requires this deployment step, even if the agent definition
did not otherwise change. A redeploy recreates the deployment; its endpoint is
expected to remain stable but can reject requests while the operation runs.
Use `list_aidp_async_operations(resource_type="AGENT")` to inspect deployment
progress or the bounded sanitized error details after a failure. Then obtain
approval for a deterministic smoke test with `invoke_aidp_agent` and report
the endpoint, session ID, and trace summary. Stop after one failed deployment
or smoke test. These tools never undeploy or delete resources.

For the approval-gated end-to-end procedure and diagnosed service behavior,
use the [`aidp-agent-deploy`](../skills/aidp-agent-deploy/SKILL.md) skill. See
[specification 013](../specs/013-agent-deploy-and-skill.md) for the detailed
acceptance criteria and live-verification status.

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
