# Local notebook to AI Data Platform job MCP

## Problem

Codex can create and edit a Jupyter notebook in this repository, but the
notebook must then be copied to an Oracle AI Data Platform (AI DP) workspace
and run as a notebook task on a selected cluster. The current repository has
separate cluster and catalog commands but no reusable operation for notebook
content or workflow jobs.

## Scope

Implement a local stdio MCP server that reuses the project's Python 3.11 AI DP
SDK, `aidp_common` configuration, and API-key authentication. Codex creates
the local `.ipynb` file directly; the MCP server never generates notebook
content.

The initial server exposes these operations:

| Tool | Side effect | Intended behavior |
| --- | --- | --- |
| `upload_notebook` | Creates or updates workspace content | Validate a local `.ipynb`, resolve the configured instance and workspace, then copy it to an explicit workspace path. |
| `ensure_notebook_job` | Creates or updates a workflow job | Reconcile one named job containing one workspace-backed notebook task attached to an explicitly selected cluster. |
| `start_notebook_job` | Creates a job run | Start a run for an existing job and optionally poll it to a terminal state. |
| `get_job_run` | Read-only | Return the current job-run and task-run status, with sanitized resource identifiers. |

The first version supports one notebook task per managed job. It uses no
schedules, Git task sources, nested jobs, arbitrary Python tasks, or job
deletion.

## Assumptions and prerequisites

* The existing `codex-4-oci-aidp` Conda environment is the server runtime; it
  already contains the AI DP SDK 4.2.1 and its compatible OCI SDK.
* Root `.env` provides `OCI_CONFIG_FILE`, `OCI_PROFILE`, `REGION`,
  `COMPARTMENT`, `AIDP_INSTANCE_ID`, `WORKSPACE_NAME`, and, where required,
  `AIDP_ENDPOINT`. Credentials remain in the OCI config and key file.
* The selected identity has AI DP permissions to read the instance/workspace
  and clusters, modify workspace content, manage jobs and create job runs. It
  must also have the applicable compute-level permission to use the cluster and
  inspect its run metadata or logs.
* The selected cluster must be active before a job run is submitted. Starting a
  cluster is outside this feature's scope.
* The local notebook is a valid, non-empty `.ipynb` file under the repository
  root. Uploading files outside the repository is rejected in the initial
  version.

## Design

The server is an adapter, not a second implementation of AI DP behavior:

```text
Codex writes local notebook
          |
          v
aidp-mcp (stdio, local Python 3.11)
          |
          +-- NotebookClient: create/update workspace content
          +-- ClusterClient: resolve and validate an active cluster
          +-- WorkflowClient: create/update job, create job run, poll status
          |
          v
AI DP workspace, workflow job, and cluster
```

`aidp_common` remains the sole place that resolves OCI configuration,
authenticates API-key requests, derives the service endpoint, and discovers
the requested AI DP instance and workspace. The server must not read notebook
paths, OCI identities, or secrets from an LLM prompt when they are available
through validated tool arguments or `.env`.

### Upload contract

`upload_notebook` accepts `local_path`, `workspace_path`, `overwrite`, and
`apply`. It canonicalizes both paths, validates the `.ipynb` extension and
JSON syntax, and computes a SHA-256 digest before contacting AI DP. With
`apply=false` (the default), it reports the resolved workspace destination and
whether creation, update, or no change would occur after read-only discovery.
With `apply=true`, it uses
the documented SDK notebook-content operation and returns only the destination
path and digest. It never prints notebook cell content.

### Job contract

`ensure_notebook_job` accepts `job_name`, `workspace_notebook_path`,
`cluster_name`, `job_location`, `max_concurrent_runs`, and `apply`. It resolves
the cluster by exact name in the selected workspace and rejects zero or
multiple matches. It creates a job if absent; otherwise it compares the
managed single notebook-task definition with the requested notebook path and
cluster, then updates only fields owned by this server. It must not silently
modify jobs with multiple tasks or unmanaged task types.

`start_notebook_job` accepts `job_name`, `wait`, `timeout_seconds`, and
`confirm_start`. It fails unless `confirm_start=true`, rechecks that the job
has exactly the supported task shape and that its cluster is active, then
creates one job run. It returns the job-run key immediately when `wait=false`.
When `wait=true`, it polls with a bounded interval and timeout, returning the
last observed state without cancelling a run on timeout.

`get_job_run` accepts a job-run key and is read-only. Output is limited to job
and task states, timestamps, sanitized error messages, and resource keys. Log
or notebook-output retrieval is deferred until a separate specification defines
size limits and sensitive-data handling.

## Non-goals

* Generating notebooks; Codex performs that local editing work.
* Starting, stopping, resizing, or creating clusters.
* Broad workspace browsing, catalog/volume access, arbitrary file upload, or
  arbitrary OCI command execution.
* Schedules, automatic retry beyond the job definition, cleanup by name
  pattern, automatic deletion of notebooks/jobs/runs, or cost estimation.
* Claiming that local tests prove AI DP runtime compatibility.

## Safety, authorization, and recovery

`upload_notebook`, `ensure_notebook_job`, and `start_notebook_job` are remote
mutations. Each must display its resolved instance, workspace, destination,
cluster, and intended change before it applies the request. Only
`start_notebook_job` requires a separate positive confirmation because it can
consume compute immediately. Tool descriptions must make this distinction
visible to Codex.

The server must use exact identifiers after discovery, bound all polling, and
record created or updated resource keys in its sanitized response. On a lost
submission response, it reports an unknown outcome and directs the user to
inspect named jobs/runs; it must never resubmit automatically. Recovery is
limited to human-reviewed deletion or update of the explicitly returned
resource keys.

## Configuration and packaging

Add the MCP framework as a pinned runtime dependency only after selecting a
version compatible with Python 3.11 and the installed AI DP SDK. Add an
`aidp_mcp/` package for the adapter and a `scripts/start_aidp_mcp.sh` launcher.
The launcher reads the root `.env` through existing settings logic, not by
sourcing it. Register the server with Codex only after local protocol tests
pass. The registration must contain no credentials, OCIDs, endpoints, or local
absolute paths beyond the launcher itself.

## Acceptance criteria

* Offline tests cover path validation, notebook validation, identifier
  validation, exact-match discovery, dry-run output, conflict handling, and
  bounded polling using mocked SDK clients.
* A protocol test starts the stdio MCP server and verifies all four tool
  schemas without cloud access.
* `upload_notebook` performs no SDK mutation when `apply=false`, and update
  logic is idempotent when the remote digest matches the local digest.
* Job reconciliation refuses unsupported existing job definitions rather than
  overwriting them.
* A live, opt-in AI DP test documents the exact resource inputs, IAM
  permissions, compute cost risk, created resource keys, result, and cleanup.
* Black, Pylint, and pytest pass in `codex-4-oci-aidp`. Live verification stays
  pending until it is explicitly authorized and successfully performed.

## Verification evidence and sources

Verified 2026-09-15:

* Oracle's Quick Start documents the same ordering: upload notebook content,
  create/update a job with a notebook task and cluster, create a job run, then
  poll the run: <https://docs.oracle.com/en/cloud/paas/ai-data-platform/aiwap/quick-start.html>
* Oracle documents local `.ipynb` import, attached compute requirements, and
  manual/scheduled job runs: <https://docs.oracle.com/en/cloud/paas/ai-data-platform/aidug/notebooks.html>
* Oracle documents notebook tasks as workspace-backed tasks that select a
  compute cluster: <https://docs.oracle.com/en/cloud/paas/ai-data-platform/aidug/configure-tasks.html>
* Oracle documents jobs and job-run tracking: <https://docs.oracle.com/en/cloud/paas/ai-data-platform/aidug/configure-jobs.html>
* The locally installed `aidp-python-client` 4.2.1 exposes `NotebookClient`
  content operations and `WorkflowClient` job/job-run operations. These APIs
  have not been invoked against AI DP for this specification.

## Implementation status

Implemented locally on 2026-09-15. The `aidp_mcp/` package provides the stdio
server and `scripts/start_aidp_mcp.sh` launches it in `codex-4-oci-aidp`.
`fastmcp==3.4.5` was installed in that environment and added to the pinned root
runtime dependencies. Codex has an enabled global registration named
`aidp-mcp`; it contains only the launcher path and the non-secret
`FASTMCP_LOG_LEVEL=ERROR` setting.

Verification completed locally without cloud access:

* Black formatted the Python implementation.
* `pytest` passed 83 tests, including 9 offline MCP adapter tests.
* Pylint rated `aidp_mcp` 10.00/10.
* A real local stdio protocol handshake discovered exactly the four specified
  tools.

No local notebook has been uploaded and no AI DP job, job run, cluster action,
or other OCI operation has been submitted. Remote verification remains pending
explicit user authorization and a scoped test notebook, workspace destination,
job name, and cluster selection.
