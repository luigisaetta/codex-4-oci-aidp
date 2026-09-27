# AI DP agent code upload and draft agent MCP tools

## Problem and scope

Specification 011 added tools to observe and invoke AI DP agents. The
hello-world agent was created manually in the Workbench UI. Codex cannot yet
put agent code on the platform or create the agent definition itself.

This specification covers the part of the agent lifecycle that **does not
require an AI Compute**:

* uploading an agent folder to the workspace;
* creating or updating a **CODE** agent in `DRAFT` state that points to the
  uploaded files.

Deployment, redeployment, compute attachment, and invocation of new
deployments are out of scope. They need a working AI Compute and follow in a
later specification.

Scope:

1. A shared module for workspace file operations, reused by notebooks and
   agents.
2. A local folder policy for agent uploads (allowed roots, exclusions, secret
   refusal, limits).
3. MCP tool `upload_aidp_agent_code`: plan by default, idempotent, with
   content-hash comparison and post-upload verification.
4. MCP tool `ensure_aidp_agent`: plan by default, creates or updates a CODE
   agent definition in place.
5. Live verification: create `hello_world_api` through the tools and compare
   it field by field with the UI-created `hello_world`.

Non-goals:

* Deploying, redeploying, attaching or detaching AI Compute, invoking new
  deployments, and `validate_agent`, whose side effects and compute
  requirements are unknown.
* Deleting agents, deployments, files, or folders. Files present remotely but
  not locally are reported, never deleted.
* Guardrails, session configuration, agent cards, and canvas agents.
* A skill for agents, which comes after the deploy tools.

## Assumptions and prerequisites

Observed live on 2026-09-27, read-only, on the UI-created `hello_world`
agent (specification 011 evidence and this analysis):

* Files uploaded from the UI are workspace objects with `type=FILE`. The agent
  folder is `type=FOLDER` at `/Workspace/hello_world`.
* The agent has:
  * `type=CODE`, `lifecycle_state=DRAFT`, `deployment_mode=NOT_DEPLOYED`;
  * `entry_file_path=/Workspace/hello_world/hello_agent.py`;
  * `dependencies_file_path=/Workspace/hello_world/requirements.txt`;
  * `path_info=/Workspace`.
* No agent JSON is visible as a workspace object under `/Workspace`, although
  the SDK describes `path_info` as the path where the agent JSON is written.
* Reading file content works with `GET …/workspaces/{key}/objects/{path}`
  when the full absolute path (`/Workspace/…`) is URL-encoded as one final
  path segment and sent through `base_client.call_api`. The response is the
  raw bytes; the SHA-256 digest matched the local file.
  * Passing the path to the generated `get_workspace_object` method returns
    404. This is the same double-encoding issue already handled for notebook
    content (`validation.encoded_content_path`).
  * The relative form (`hello_world/hello_agent.py`) also worked. Use the
    absolute form for consistency.
* `AgentClient` list calls must use a page size of at most 100. Larger values
  return HTTP 500 (specification 011 evidence).

Verified in the pinned SDK `aidp-python-client` 4.2.1:

* `WorkspaceObjectClient.create_workspace_object(...)`:
  * `POST …/objects`, with headers `path`, `type`, `is-overwrite`, and
    `is-upload-file-base64-encoded`;
  * a raw request body.

  The existing `notebooks.create_workspace_folder` already uses this endpoint
  through `call_api` with `type: FOLDER` and an empty octet-stream body.
* `AgentClient.create_agent(ai_data_platform_id, workspace_key,
  create_agent_details, **kwargs)` with `CreateAgentDetails`:
  * `display_name` and `path_info` are required;
  * optional fields include `type` (`CANVAS`/`CODE`), `entry_file_path`,
    `dependencies_file_path`, `description`, and `compute_key`.
* `AgentClient.update_agent(..., agent_key, update_agent_details, **kwargs)`
  accepts `if_match`, with `UpdateAgentDetails` fields including
  `entry_file_path`, `dependencies_file_path`, and `description`.

Documented limits for agent code upload
([agent-creation](https://docs.oracle.com/en/cloud/paas/ai-data-platform/aidug/agent-creation.html)):
at most 500 files, 500 MB per file, and 5 GB in total.

To be discovered live and recorded in "Verification evidence":

* the exact upload header values that make a file appear as `type=FILE` with
  identical content;
* whether `create_agent` accepts `path_info=/Workspace` and produces the same
  fields as the UI;
* whether `update_agent` requires `if_match`, and what the service returns
  without it.

## Interfaces and behavior

### 1. Shared workspace file module

Add `aidp_mcp/workspace_files.py` as a shared module:

* **Move, do not copy,** `create_workspace_folder` and
  `is_existing_folder_error` from `notebooks.py`. Update the notebook code
  and its tests to import them from the shared module. Notebook behavior and
  outputs stay identical.
* Add `read_workspace_file(client, instance_id, workspace_key, path)`:
  * returns the raw bytes, or `None` for a missing file;
  * uses the encoded-final-segment `call_api` form described above;
  * treats a 404 as a missing file; any other error is re-raised.
* Add `upload_workspace_file(client, instance_id, workspace_key, path, data,
  overwrite)`:
  * `POST …/objects` with `path`, `type: FILE`, and `is-overwrite`;
  * a binary body;
  * no retry.

  Header values that turn out to be required, such as the content type or the
  base64 flag, are recorded in the evidence.
* Workspace paths are validated with the existing validators and must stay
  under `/Workspace`. Reject `..` segments and control characters.

The client used is the one already used for workspace objects, following
`targets` conventions. If a dedicated `WorkspaceObjectClient` context is
needed, add it to `targets.py`.

### 2. Local agent folder policy

Add to `local_files.py`:

* `validate_local_directory(local_dir, roots)`: same rules as
  `validate_local_path`:
  * absolute path required when there is more than one root, or when the
    only root is not the repository;
  * resolved before containment checks;
  * must be an existing directory.
* `collect_agent_files(directory)` returns the files to upload, sorted by
  relative POSIX path:
  * **Skipped silently** (build and tool artifacts): `__pycache__/`,
    `.pytest_cache/`, `.git/`, `.venv/`, `*.pyc`, `.DS_Store`.
  * **Refused** (the whole upload fails and names the offending relative
    paths): `.env` and `.env.*`, `*.pem`, `*.key`, `id_rsa*`, `.oci/`, and
    `*.p12`. Refusing, instead of skipping, makes the user remove secrets
    deliberately.
  * **Refused:** symbolic links anywhere inside the folder.
  * **Refused:** empty folders with no uploadable files.
  * Limits: at most 500 files, 500 MB per file, and 5 GB in total, reported
    with actual counts.

### 3. `upload_aidp_agent_code`

Signature:
`upload_aidp_agent_code(local_dir: str, workspace_dir: str, *, overwrite: bool = False, apply: bool = False)`.

* `workspace_dir` is an absolute workspace folder, for example
  `/Workspace/hello_world_api`.
* **Plan (`apply=false`, default):**
  * read each target file with `read_workspace_file`;
  * classify it as `create` (missing), `unchanged` (same SHA-256), or
    `update` (different);
  * list `remote_only` files: files in the target folder, not recursive
    beyond the uploaded tree, that do not exist locally;
  * no writes.
* **Apply (`apply=true`):**
  * refuse if any file is `update` and `overwrite` is not `true`, before any
    write;
  * create the missing folders in parent-first order with
    `create_workspace_folder`;
  * upload `create` and `update` files in sorted order;
  * after each upload, read the file back and verify the SHA-256. A mismatch
    stops the operation with an error.
  * `unchanged` files are not uploaded;
  * uses `safety.should_apply` for the no-op case (everything `unchanged`).
* **Output (bounded):**
  * `local_root` (folder name only) and `workspace_dir`;
  * counts per action;
  * `files`: the relative path, action, size, and first 12 characters of the
    SHA-256, at most 500 entries;
  * `remote_only`, at most 100 entries;
  * with `apply=true`, `uploaded` and `verified` counts.

  No absolute local paths and no file content.
* **Partial failure:** uploads are not atomic. On an error during apply,
  raise `AidpError` naming the file that failed and listing the files already
  uploaded, so that a rerun, which is idempotent, can complete the work.
* The docstring starts with "AI DP agent code upload". It states that the
  default is plan-only, that updates require `overwrite=true`, and that
  secrets are refused.

### 4. `ensure_aidp_agent`

Signature:
`ensure_aidp_agent(agent_name: str, workspace_dir: str, entry_file: str, *, dependencies_file: str | None = None, description: str | None = None, apply: bool = False)`.

* `entry_file` and `dependencies_file` are **relative to `workspace_dir`**,
  for example `hello_agent.py`. The tool builds the absolute workspace paths.
  In plan mode it verifies that both files exist remotely, and fails if they
  do not.
* Resolve the agent by exact name with the existing lookup:
  * **Absent** → planned action `create`, with
    `CreateAgentDetails(display_name=agent_name, type="CODE",
    path_info="/Workspace", entry_file_path=…, dependencies_file_path=…,
    description=…)`. Do not set `compute_key`.
  * **Present with `type` other than `CODE`** → `AidpError`. Never convert a
    canvas agent.
  * **Present CODE agent with identical `entry_file_path`,
    `dependencies_file_path`, and `description`** (when given) → `unchanged`.
  * **Present CODE agent with differences** → `update`, sending only the
    changed fields in `UpdateAgentDetails`. Pass `if_match` with the agent
    ETag from `get_agent` when the response provides one. Never modify
    compute, guardrails, session, or card settings.
* **Plan output:**
  * the `action` and the current and desired values of the three fields;
  * the agent's `lifecycle_state` and `deployment_mode`;
  * a note when the agent is deployed, stating that changes take effect only
    after a redeploy, which is not handled by this tool.
* **Apply:** perform the create or update through `safety.should_apply`, then
  read the agent again and return the same fields as `get_aidp_agent`.
* The docstring starts with "AI DP agent definition". It states plan by
  default, CODE only, and that no deployment happens.

### Page size and dependency rules

* Every SDK list call uses at most the shared page size of 100.
* `workspace_files.py` is a shared module. `agents.py` and `notebooks.py`
  import it, and neither imports the other.

## API design and permissions

* New writes:
  * workspace object create (folders and files) under the configured
    workspace;
  * `create_agent` and `update_agent` for CODE agents.

  The user needs write permission on the workspace folder and permission to
  create and update agents.
* No compute is attached and nothing is deployed, so no compute cost is
  incurred.
* **Cleanup:** the tools never delete. Test resources (the `hello_world_api`
  agent and `/Workspace/hello_world_api`) are removed manually in the
  Workbench UI after verification, and the evidence states whether this was
  done.

## Acceptance and verification

Offline tests, with mocked clients and network blocked:

* **Local policy:**
  * artifacts are skipped;
  * each secret pattern is refused, with its relative path named;
  * symbolic links are refused;
  * the file, per-file, and total limits are enforced;
  * relative `local_dir` values are rejected under the allowed-roots rules;
  * a folder outside every root is rejected.
* **Upload plan:**
  * create, update, unchanged, and remote_only classification;
  * no write calls in plan mode;
  * digests compare raw bytes.
* **Upload apply:**
  * `update` without `overwrite` fails before any write;
  * folders are created parent-first;
  * `unchanged` files are skipped;
  * read-back verification detects a mismatch;
  * a mid-upload failure reports the uploaded files;
  * a rerun after full success reports only `unchanged`.
* **`ensure_aidp_agent`:**
  * create builds exactly the documented `CreateAgentDetails` fields, with
    `path_info="/Workspace"` and no `compute_key`;
  * unchanged detection;
  * an update sends only the changed fields and `if_match`;
  * a canvas agent is refused;
  * missing remote files fail in plan mode;
  * a deployed agent produces the redeploy note.
* **Notebook regression:** the existing notebook tests pass unchanged after
  the helpers move.
* **MCP snapshot:** updated intentionally with the two new tools. Existing
  tools stay byte-identical.
* **Quality gates:**
  * full suite green;
  * Pylint 10.00/10 with no disables;
  * Black clean;
  * `git diff --check` clean.

Manual verification, explicitly authorized by the user, recorded without keys
or OCIDs:

1. `upload_aidp_agent_code` with
   `local_dir=/Users/…/agents-4-ai-dp/agents/hello_world` and
   `workspace_dir=/Workspace/hello_world_api`:
   * plan: both files are `create`;
   * apply: both uploaded and verified;
   * `list_notebooks` is not useful here. Confirm in the UI or with a
     read-only listing that both appear as `type=FILE`.
2. Run the same upload plan again: both files are `unchanged`.
3. `ensure_aidp_agent("hello_world_api", "/Workspace/hello_world_api",
   "hello_agent.py", dependencies_file="requirements.txt")`: plan, then
   apply.
4. `get_aidp_agent` on `hello_world_api` and `hello_world`. Compare `type`,
   `lifecycle_state`, `deployment_mode`, `path_info`, the path formats, and
   the compute attachment; record any difference. In the UI, confirm that
   `hello_world_api` shows the entry and dependency files as set.
5. Run the same `ensure_aidp_agent` again: `unchanged`.
6. Change a comment in the local `hello_agent.py`:
   * the upload plan shows one `update`;
   * apply without `overwrite` is refused;
   * apply with `overwrite=true` uploads and verifies the file.

   Revert the local change afterwards.
7. Negative check: add a temporary `.env` file to a copy of the folder and
   confirm that the upload plan is refused with that relative path named.
   Delete the copy afterwards.
8. Cleanup: delete `hello_world_api` and `/Workspace/hello_world_api` in the
   UI, or keep them deliberately for the future deploy tests. Record the
   choice.

## Execution plan

One commit per step. Every step keeps the suite green and Pylint at 10.00/10:

1. `workspace_files.py`:
   * move the folder helpers from `notebooks.py`;
   * add `read_workspace_file` and `upload_workspace_file`;
   * add the client context if needed;
   * include tests.
2. `local_files.validate_local_directory` and `collect_agent_files`, with
   tests.
3. `upload_aidp_agent_code` in `agents.py`, plus its tool and tests.
4. `ensure_aidp_agent` in `agents.py`, plus its tool and tests.
5. Documentation: the `aidp_mcp/README.md` tool table and code layout, the
   `agents-4-ai-dp` workflow note if useful, `CHANGELOG.md`, and the final
   snapshot confirmation.

Finish each step, then commit. Never discard working changes or reset this
file. Stop only on a real blocker, keeping the work in the tree and
describing the blocker in "Verification evidence".

## Verification evidence

2026-09-27, execution-plan step 1:

* Implemented `workspace_files.py` with a dedicated, request-scoped
  `WorkspaceObjectClient` supplied by `workspace_clients`. It preserves the
  existing folder-creation behavior and adds encoded-final-segment binary file
  reads and raw binary file uploads without retries.
* Offline tests cover the folder conflict behavior, encoded file read, missing
  file handling, binary upload headers and body, workspace-path safety, and
  the dedicated client context. Notebook regression tests passed locally.
* No workspace folder or file was created remotely. The exact production file
  upload header requirements remain pending live verification.

2026-09-27, execution-plan step 2:

* Implemented allowed-root validation for local agent directories and a
  deterministic agent-file collection policy. Build artifacts are skipped;
  secret-like paths and symbolic links are refused before any remote work.
* Offline tests cover each secret pattern, artifact exclusion, symbolic links,
  empty folders, allowed-root boundaries, and file-count, per-file, and total
  byte limits using small local fixtures.
* No AI DP operation was attempted. The policy is locally verified only.

2026-09-27, execution-plan step 3:

* Implemented `upload_aidp_agent_code` as a plan-by-default MCP tool. It
  compares local and remote SHA-256 digests, reports bounded remote-only
  workspace files, and only creates folders or uploads files when `apply=true`.
* Offline tests cover create, update, unchanged, remote-only, overwrite
  refusal before writes, parent-first folders, post-upload digest verification,
  mid-upload failure reporting, no-op reruns, and page requests capped at 100.
* No remote upload was run. The exact production `FILE` upload headers and
  object appearance remain pending explicit live verification.

2026-09-27, execution-plan step 4:

* Implemented MCP tool `ensure_aidp_agent` and its `agents.ensure_agent`
  operation. It verifies the referenced workspace files before planning;
  creates only a CODE definition with `path_info=/Workspace` and no compute;
  and minimally updates only entry path, dependency path, and an explicitly
  supplied description. Existing non-CODE agents are refused. The plan reports
  current and desired fields, lifecycle state, deployment mode, and the
  redeploy limitation for deployed agents.
* Offline acceptance tests use mocked `aidp-python-client` 4.2.1 clients and
  workspace object reads. They cover exact creation fields without compute,
  unchanged plans, ETag-protected minimal updates, canvas refusal, missing
  remote files before agent lookup, the deployed-agent redeploy note, and the
  MCP schema snapshot. `conda run -n codex-4-oci-aidp pytest -q` passed 255
  tests; `black --check .`, the configured full Pylint command (10.00/10), and
  `git diff --check` passed locally.
* This is local mocked verification only; no `apply=true` call, agent create,
  or agent update was made on OCI AI DP. Live confirmation of the service's
  `create_agent` and `update_agent` behavior, including ETag handling, remains
  pending the explicit verification procedure above.
