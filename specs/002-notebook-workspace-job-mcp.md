# Local notebook to AI Data Platform job MCP

## Problem

Codex can create and edit a Jupyter notebook in this repository, but the
notebook must then be copied to an Oracle AI Data Platform (AI DP) workspace
and run as a notebook task on a selected cluster. The current repository has
separate cluster and catalog commands but no reusable operation for notebook
content, notebook discovery, or workflow jobs.

## Scope

Implement a local stdio MCP server that reuses the project's Python 3.11 AI DP
SDK, `aidp_common` configuration, and API-key authentication. Codex creates
the local `.ipynb` file directly; the MCP server never generates notebook
content.

The initial server exposes these operations:

| Tool | Side effect | Intended behavior |
| --- | --- | --- |
| `upload_notebook` | Creates or updates workspace content | Validate a local `.ipynb`, resolve the configured instance and workspace, then copy it to an explicit workspace path. |
| `list_notebooks` | Read-only | Return bounded metadata for notebook objects in one explicit workspace directory, optionally filtered by a local name substring. |
| `find_notebook_jobs` | Read-only | Return bounded, sanitized workflow-job metadata for jobs with a workspace notebook task that references one exact notebook. |
| `ensure_notebook_job` | Creates or updates a workflow job | Reconcile one named job containing one workspace-backed notebook task attached to an explicitly selected cluster. |
| `start_notebook_job` | Creates a job run | Start a run for an existing job and optionally poll it to a terminal state. |
| `get_job_run` | Read-only | Return the current job-run and task-run status, with sanitized resource identifiers. |
| `get_cluster_status` | Read-only | Resolve one exact cluster in the configured workspace and return a small, sanitized configuration and state summary. |
| `set_cluster_state` | Start or stop a cluster | Explicitly request `start` or `stop` for one exact configured-workspace cluster, with optional bounded waiting. |
| `get_job_run_output` | Read-only | Fetch bounded, textual output for the one task in a managed single-notebook job run. |

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
* The selected cluster must be active before a job run is submitted. When it is
  stopped, callers may explicitly use `set_cluster_state(..., "start",
  wait=true, confirm_action=true)` and only submit the job after it reports
  `completed` with state `ACTIVE`.
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
          +-- NotebookClient base client: list filtered workspace notebook objects
          +-- ClusterClient: resolve and validate an active cluster
          +-- ClusterClient: explicitly start or stop a selected cluster
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
`apply`. The workspace path is relative to the selected workspace root (for
example, `notebooks/test00/test00.ipynb`). The server constructs its absolute
`/Workspace/...` service path and URL-encodes it before passing it to the SDK.
For a new notebook it creates or retains the parent folder, creates an empty
notebook, renames it to the requested path, and uploads its JSON content. It
canonicalizes both paths, validates the `.ipynb` extension and
JSON syntax, and computes a SHA-256 digest before contacting AI DP. With
`apply=false` (the default), it reports the resolved workspace destination and
whether creation, update, or no change would occur after read-only discovery.
With `apply=true`, it uses
the documented SDK notebook-content operation and returns only the destination
path and digest. It never prints notebook cell content.

### Notebook discovery contract

`list_notebooks` accepts an absolute `path` rooted at `/Workspace`, optional
`name_contains`, and `max_results` from 1 through 1,000 (default 100). It
uses Oracle's documented workspace-objects list endpoint with `type=NOTEBOOK`
and follows OCI pagination only until the local result bound is reached. The
operation lists the explicit directory only; it is not recursive. The optional
name filter is a case-insensitive substring match applied locally because the
documented remote `displayName` filter is exact-match only.

The response contains only each matching object's display name, full path,
type, creation time, and update time, plus the requested path and a truncation
indicator. It never returns notebook content, creator identity, descriptions,
metadata, tags, ETags, or arbitrary SDK response fields. It does not create,
modify, execute, or delete any AI DP resource.

### Job contract

`find_notebook_jobs` accepts an absolute notebook path rooted at `/Workspace`
and `max_results` from 1 through 1,000 (default 100). It lists jobs in the
configured workspace through the documented SDK `list_jobs` operation, then
gets candidate job definitions because job-list summaries do not contain task
definitions. It follows pagination only until the local match bound is reached.
It matches only tasks whose type is `NOTEBOOK_TASK`, source is `WORKSPACE`, and
notebook path equals the requested path after normalizing the SDK's relative
task paths to `/Workspace/...`. It returns the requested notebook path, matching
job key, name, storage path, and matching task key and cluster key, plus a
conservative truncation indicator. It does not return descriptions, schedules,
parameters, identities, tags, arbitrary task details, or job-run history. The
operation never creates, modifies, runs, or deletes a resource.

`ensure_notebook_job` accepts `job_name`, `workspace_notebook_path`,
`cluster_name`, `job_location`, `max_concurrent_runs`, and `apply`. It resolves
the cluster by exact name in the selected workspace and rejects zero or
multiple matches. It creates a job if absent; otherwise it compares the
managed single notebook-task definition with the requested notebook path and
cluster, then updates only fields owned by this server. It must not silently
modify jobs with multiple tasks or unmanaged task types.

The job name must start with a letter and contain only letters, numbers, or
underscores, matching the AI DP resource-name constraint. Each generated
notebook task explicitly sets `runIf` to `ALL_SUCCESS`, including the
single-task case, because AI DP rejects a null task run condition.

`start_notebook_job` accepts `job_name`, `wait`, `timeout_seconds`, and
`confirm_start`. It fails unless `confirm_start=true`, rechecks that the job
has exactly the supported task shape and that its cluster is active, then
creates one job run. It returns the job-run key immediately when `wait=false`.
When `wait=true`, it polls with a bounded interval and timeout, returning the
last observed state without cancelling a run on timeout.

`get_job_run` accepts a job-run key and is read-only. Output is limited to job
and task states, timestamps, sanitized error messages, and resource keys.

`get_cluster_status` accepts an exact `cluster_name`, rejects zero or multiple
visible matches, then gets the resolved cluster. It returns its key, display name,
type, state, state details, runtime version, node type, driver and worker
configuration, and auto-termination setting. It omits endpoints, log identifiers,
attached notebook/session lists, and arbitrary configuration objects.

`set_cluster_state` accepts an exact `cluster_name`, an action of `start` or
`stop`, `wait`, `timeout_seconds`, and `confirm_action`. It requires
`confirm_action=true` before performing discovery or mutation. It starts only a
`STOPPED` cluster and stops only an `ACTIVE` cluster; an already desired state
is a successful no-op, and an existing `STARTING` or `STOPPING` transition is
not resubmitted. Other states fail safely. The request uses the detail response
ETag when available, `NoneRetryStrategy`, and one unique retry token. A
transport failure has an unknown outcome and must be followed by
`get_cluster_status`, never an automatic retry. The API's accepted response is
reported as `accepted`; only `wait=true` and a `completed` outcome prove the
requested state. Polling is bounded (default 1,200 seconds), and timeout never
cancels the cloud operation.

`get_job_run_output` accepts a job-run key and an optional `max_characters`
value from 1 through 12,000 (default 12,000). It resolves the task runs and
refuses runs that do not have exactly one task run, preserving the server's
managed-single-task boundary. It fetches only the task run's recorded output
key. The response includes metadata and at most `max_characters` from each
uncompressed, non-base64 `TEXT_PLAIN` output item and its error trace. It
does not decode or return notebook, HTML, image, binary, compressed, base64,
file-path, or output-parameter values. It reports truncation caused by either
AI DP or the local character bound. Task output can still contain sensitive
application data: callers must request it only when that data is authorized
for the active Codex session.

## Non-goals

* Generating notebooks; Codex performs that local editing work.
* Resizing, creating, restarting, or automatically starting/stopping clusters
  as a side effect of job submission.
* Recursive workspace browsing, catalog/volume access, arbitrary file upload,
  or arbitrary OCI command execution.
* Schedules, automatic retry beyond the job definition, cleanup by name
  pattern, automatic deletion of notebooks/jobs/runs, or cost estimation.
* Claiming that local tests prove AI DP runtime compatibility.

## Safety, authorization, and recovery

`upload_notebook`, `ensure_notebook_job`, `start_notebook_job`, and
`set_cluster_state` are remote
mutations. Each must display its resolved instance, workspace, destination,
cluster, and intended change before it applies the request. Only
`start_notebook_job` requires a separate positive confirmation because it can
consume compute immediately. `set_cluster_state` also requires a separate
positive confirmation: start can incur compute charges, while stop can
interrupt workloads. Tool descriptions must make these distinctions visible to
Codex.

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
* A protocol test starts the stdio MCP server and verifies all nine tool
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
* The AI DP REST reference documents cluster retrieval and task-run output
  retrieval operations: <https://docs.oracle.com/en/cloud/paas/ai-data-platform/aiwap/rest-endpoints.html>
* Oracle's cluster REST reference documents the `start` and `stop` actions and
  their cluster-specific endpoints (verified 2026-09-16):
  <https://docs.oracle.com/en/cloud/paas/ai-data-platform/aiwap/api-cluster.html>
* Oracle's workspace-objects REST reference documents the required `path`,
  `type` filter, pagination, and summary response fields (verified 2026-09-16):
  <https://docs.oracle.com/en/cloud/paas/ai-data-platform/aiwap/op-aidataplatforms-aidataplatformid-workspaces-workspacekey-objects-get.html>
* The locally installed `aidp-python-client` 4.2.1 exposes `NotebookClient`
  content operations and `WorkflowClient` job/job-run operations. These APIs
  have not been invoked against AI DP for this specification.

Verified 2026-09-16 for notebook-to-job discovery:

* The installed `aidp-python-client` 4.2.1 `WorkflowClient.list_jobs` supports
  workspace-scoped pagination and returns `JobCollection` summaries; `get_job`
  returns the task definitions needed to inspect `NotebookTask.notebook_path`
  and `source`.
* Oracle's Quick Start constructs a job with a `NOTEBOOK_TASK`, `WORKSPACE`
  source, and notebook path before running it:
  <https://docs.oracle.com/en/cloud/paas/ai-data-platform/aiwap/quick-start.html>

## Implementation status

Implemented locally on 2026-09-16: `list_notebooks` adds bounded, read-only,
non-recursive discovery of notebook metadata in an explicit `/Workspace`
directory. It uses the documented workspace-objects endpoint with
`type=NOTEBOOK`; the installed AI DP SDK 4.2.1 does not expose this operation
as a typed `WorkspaceClient` method, so the server uses the configured,
authenticated generated client's base client, as it already does for notebook
content paths. Offline tests cover path and result-bound validation, exact
request construction, pagination, local case-insensitive filtering, response
sanitization, and MCP tool registration. Local quality-check results are
recorded: Black completed without changes, pytest passed 117 offline tests,
and Pylint rated `aidp_mcp` 10.00/10. Remote verification remains pending and
requires an explicitly authorized read-only request.

Implemented locally on 2026-09-16: `find_notebook_jobs` adds bounded,
read-only discovery of workflow jobs that reference one exact workspace
notebook. It lists job summaries and retrieves only candidate job definitions
to inspect notebook tasks, normalizing relative task paths for comparison.
Offline tests cover path validation, matching, task filtering, pagination,
result bounding, response sanitization, and MCP registration. Black completed
without further changes, the full offline suite passed 126 tests, and Pylint
rated `aidp_mcp` 10.00/10. Remote verification remains pending and requires an
explicitly authorized read-only request.

Implemented locally on 2026-09-16: `set_cluster_state` adds an explicitly
confirmed start/stop lifecycle operation. Offline tests cover tool registration,
confirmation, state no-op behavior, typed start/stop SDK submissions, ETag
forwarding, no-retry submission, and timeout input validation. Black, Pylint,
and the full offline test suite passed; no cluster lifecycle action was made
against OCI, so live lifecycle verification remains pending.

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
* A real local stdio protocol handshake initially discovered exactly the four
  specified
  tools.

2026-09-15: Added `get_cluster_status` and `get_job_run_output`. Both use only
read operations in the installed `aidp-python-client` 4.2.1 SDK. The latter
uses `list_task_runs` and `fetch_output` only after validating the job-run key,
single-task shape, output key, and local character bound. Offline MCP tests
passed (24 tests) and Pylint rated `aidp_mcp` 10.00/10. No new AI DP API call
was made; remote verification remains pending and must be explicitly
authorized.

On 2026-09-15, the first authorized remote upload dry-run identified a local
configuration defect: `WORKSPACE_NAME` was present in `.env.example` but was
not exposed by the shared connection parser. The parser was corrected and its
offline coverage now verifies the setting. The failed dry-run made no remote
mutation. A new dry-run is required before upload.

The first write attempt on 2026-09-15 was rejected by OCI with
`NotAuthorizedOrNotFound` before a notebook was created. Inspection of the
installed AI DP SDK showed that it appends `content_path` directly to the
notebook-content endpoint. The prior `/Workspace/...` validation introduced a
double slash in that request and was corrected to require a workspace-relative
path. Offline validation coverage now rejects absolute paths. A dry-run with
the corrected path is required before another write.

The generated Python SDK double-encodes `%2F` when it is passed through its
`content_path` parameter. The server now uses the SDK client's authenticated
base client with Oracle's documented notebook-content URL template and a
single encoded final path segment. It creates or retains the parent workspace
folder, creates the empty notebook, renames it and uploads content. The
service returns a specific HTTP 500 `InternalError` message when a GET targets
an absent notebook; only that response and HTTP 404 are interpreted as the
absent-content case. Other service errors stop the operation.

All generated AI DP clients used by the MCP server retain response timestamps
as opaque values. This accommodates deployments that return numeric timestamps
and avoids OCI SDK datetime deserialization failures during job discovery;
the server does not interpret those values.

Remote upload verification was attempted on 2026-09-15 with the configured
instance, workspace and API-key credentials. The read-only dry-run resolved
the selected resources and reported a `create` action for
`notebooks/test00/test00.ipynb`. The subsequent `update_content` request to
the SDK's corrected workspace-relative endpoint was rejected by OCI with
`NotAuthorizedOrNotFound` (HTTP 404). OCI therefore did not confirm creation
of the notebook. The service uses this code for both absent resources and
authorization failures; because the dry-run reached the workspace, missing
write permission on notebook content is the leading diagnosis, not a verified
conclusion. No retry was made.

Before another upload attempt, an administrator should verify that the
configured OCI identity has the least-privilege AI DP authorization to modify
notebook content in the selected instance and workspace. After that change,
repeat the read-only dry-run and exactly one `apply=true` request with
`overwrite=false`; if the outcome is uncertain, inspect the explicit notebook
path in the AI DP UI before any retry. No cleanup is required because no
notebook creation was confirmed.

No notebook creation has been confirmed, and no AI DP job, job run, cluster
action, or other OCI operation has been submitted. The corrected workflow has
passed local verification but requires an explicitly authorized remote retry.

On 2026-09-15, an authorized job-creation attempt established two AI DP
workflow validation requirements. A hyphenated job name was rejected before
creation; the MCP server now validates the documented observed name constraint
locally. A valid-name attempt was then rejected because its notebook task sent
a null `runIf`; the builder now sends `ALL_SUCCESS` and has offline regression
coverage. Neither attempt created or updated a job. Codex must be restarted
before the registered stdio MCP server can load this change; only then may the
authorized job plan, creation, and run submission be retried.

On 2026-09-15, an authorized retry after the remote notebook was manually
deleted resolved a `create` action but stopped at the workspace folder step.
AI DP returned HTTP 409 `Conflict` with `Directory already exists` for the
retained `notebooks/test00` folder; no new notebook content was uploaded. The
folder creation helper now recognizes only that documented observed response
as an idempotent success and still raises all other errors. Its offline test
covers that condition. Black, Pylint (10.00/10), and the 12 targeted offline
tests passed. A subsequent read-only dry-run reported `create`, and one
authorized `apply=true`, `overwrite=false` upload then completed successfully
for `notebooks/test00/test00.ipynb`. No job, job run, or cluster action was
submitted.

On 2026-09-15, a read-only `ensure_notebook_job` plan for the uploaded
`test00` notebook and cluster `clu02` reached AI DP but failed before returning
a plan because `WorkflowClient` tried to parse a numeric timestamp as a date.
The MCP client setup was updated to preserve timestamps for all generated AI DP
clients, matching the existing project handling for this observed service
behavior. Local regression coverage passed; restart Codex so its registered
stdio MCP server loads the change, then repeat the read-only plan before
creating the job. No job was created or updated by the failed plan.

On 2026-09-15, an authorized read-only plan for `test00_job` resolved a
`create` action for `notebooks/test00/test00.ipynb` on cluster `clu02`.
The subsequent authorized apply operation created workflow job key
`6964b0ee-02a8-4751-ae1d-85c3f64d8892`. One explicitly confirmed job run was
then submitted with key `f2443369-1e33-4f92-8009-642369b1e627`; AI DP returned
the initial state `SUBMITTED`. A subsequent read-only status query returned
the terminal state `SUCCESS`, with no state message. No cluster lifecycle
operation was performed.
