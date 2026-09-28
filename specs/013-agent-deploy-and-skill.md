# AI DP agent deploy, redeploy, and the agent workflow skill

## Problem and scope

Specifications 011 and 012 let Codex do the following on AI DP:

* upload agent code;
* create a CODE agent;
* invoke a deployed agent;
* inspect its sessions and traces.

Two parts of the lifecycle are still manual, or done with ad hoc scripts:

1. **Deploy and redeploy.** Uploading new code does not change what a
   deployed endpoint runs. The deployment executes a copy of the code taken
   at deploy time. The Workbench UI offers only deploy and undeploy, with no
   in-place redeploy. During the 2026-09-28 verification, a redeploy was
   performed with an ad hoc SDK script, outside the guarded tools.
2. **Workflow knowledge.** The correct sequence is: test locally, upload,
   define the agent, deploy or redeploy, smoke-test, then diagnose on
   failure. It has pitfalls that are known only from live verification.
   Codex has no skill that encodes this sequence.

Scope:

1. MCP tool `deploy_aidp_agent`, planned by default, for both the first
   deployment and redeployments, with bounded waiting and verification.
2. Read-only MCP tool `list_aidp_async_operations`, to observe progress and
   failures of platform operations. Today these include agent deployments and
   AI Compute creation.
3. Operational skill `aidp-agent-deploy` that orchestrates the full workflow
   with the existing tools and records the verified pitfalls.
4. Documentation, the MCP contract snapshot, and live verification.

Non-goals:

* Undeploying or deleting deployments, agents, files, or computes. The tools
  never delete.
* Creating, starting, or stopping AI Compute. Existing `set_cluster_state`
  covers start and stop.
* OAuth2-protected deployments, custom session retention, and TEST (Playground)
  deployments. Only the default PROD deployment with AIDP authentication is in
  scope.
* An agent-authoring skill (LLM factory, contract, and project layout). It is
  planned separately, after the LLM phase.
* Exposing compute logs (OCI Logging). They are observed and documented below,
  but no tool is added.

## Assumptions and prerequisites

### Verified in the pinned SDK `aidp-python-client` 4.2.1 (2026-09-28)

* `AgentClient.deploy_agent(ai_data_platform_id, workspace_key, agent_key,
  deploy_agent_details)`:
  * `POST …/agents/{agentKey}/deployments/actions/deploy`;
  * `DeployAgentDetails`: `agent_key` (required), `agent_compute_key`,
    `display_name`, `description`, `session_retention_config`,
    `o_auth_config`;
  * returns `AgentDeployment`.
* `AgentClient.redeploy_agent_by_key(ai_data_platform_id, workspace_key,
  agent_key, update_agent_deployment_details)`:
  * `POST …/deployments/actions/redeploy`;
  * `UpdateAgentDeploymentDetails`: `agent_key` (required),
    `agent_compute_key`, `display_name`, `description`, `o_auth_config`;
  * accepts `if_match`;
  * returns `AgentDeployment`.
* `AgentDeployment`:
  * `deployment_type` is one of `TEST`, `PROD`, `CODE`;
  * `lifecycle_state` is one of `CREATING`, `ACTIVE`, `INACTIVE`, `FAILED`,
    `DELETED`.
* `AsyncOperationsClient.list_async_operations(ai_data_platform_id, …)`:
  * **requires `resource_type` or `status`**, otherwise HTTP 400;
  * `get_async_operation(ai_data_platform_id, key)`;
  * operation fields: `key`, `resource_type`, `action_type`, `resource_name`,
    `resource_display_name`, `created_by_name`, `time_started`,
    `time_finished`, `status`, `status_details`, `error_code`,
    `error_message`.

### Observed live (2026-09-28)

**Redeploy behavior:**
* `redeploy_agent_by_key` with `agent_key` and the `aicomp02` compute key
  succeeded.
* The deployment is **recreated**: `time_created` changed from 06:11:03Z to
  07:17:33Z, while `time_updated` and `deployment_version` stayed `null`.
* The `endpoint_url` stayed identical. It embeds the agent key and already
  ends with `/chat`.
* The new code ran after the redeploy (the error-path test).

**Async operations:**
* `resource_type="AGENT"` lists deployments as `action_type="DEPLOY_AGENT"`.
  Other values tried (`AGENT_DEPLOYMENT`, `AGENT_FLOW`, `DEPLOYMENT`) return
  HTTP 400.
* The operation display name has the form
  `<agentKey>_<PROD|TEST>_<epoch-ms>`.
* Durations: API redeploy 38 s; UI deploy 43 s; Playground attach 74 s. The
  Playground attach is recorded as a **TEST** deployment.
* `resource_type="AI_COMPUTE"` lists `CREATE_CLUSTER` and `DELETE_CLUSTER`,
  with `status_details` such as "Completed 5 of 9 steps" and, on failure,
  `error_code`/`error_message`.

**Other live findings:**
* Attaching a compute and using the Playground changes only
  `compute_attached`. The agent stays `DRAFT`/`NOT_DEPLOYED`.
* After the first PROD deployment, the agent shows:
  * `lifecycle_state=DEPLOYED`, `deployment_mode=DEPLOYED`, `uri_state=ACTIVE`;
  * one deployment with `lifecycle_state=ACTIVE` and `deployment_type=PROD`.
* AI Compute clusters are found only with `list_clusters(type="AI_COMPUTE")`.
* Creating an AI Compute failed with a generic `InternalError` while the AI DP
  Autonomous AI Lakehouse was `STOPPED`, and succeeded once it was started.
* On user-code failure, `/chat` returns HTTP 400 with
  `AIDP_USER_CODE_EXECUTION_ERROR` and a generic message. The exception text
  is not available through sessions, traces, or the agent runtime logs in OCI
  Logging.
* The agent runtime writes deployment events to OCI Logging, for example
  `deployment.artifact.success` and "no dependencies in configured file;
  skipping pip install". They go to the compute log of the workspace, in the
  AI DP log group of the instance compartment.

### To be verified live and recorded

* The HTTP status of `deploy_agent` and `redeploy_agent_by_key`, and whether
  the response headers include an async-operation key.
* Whether a redeploy works without `agent_compute_key`, reusing the
  deployment's compute.
* Whether the endpoint rejects or fails requests while a redeploy is in
  progress.

## Interfaces and behavior

### 1. `list_aidp_async_operations` (read-only)

Signature:
`list_aidp_async_operations(resource_type: str, *, status: str | None = None, name_contains: str | None = None, max_results: int = 25)`.

Parameters:
* `resource_type` is required and restricted to the values verified to work:
  `AGENT`, `AI_COMPUTE`, `CLUSTER`. Any other value is rejected locally,
  before the call.
* `status`, when given, is one of `ACCEPTED`, `IN_PROGRESS`, `SUCCEEDED`,
  `FAILED`, `CANCELED`. Record the list actually accepted by the service.
* `name_contains` is a local, case-sensitive filter on
  `resource_display_name`, used for example to select an agent key.

Result:
* newest-first, bounded operations;
* fields: `key`, `resource_type`, `action_type`, `resource_display_name`,
  `created_by_name`, `time_started`, `time_finished`, `duration_s`, `status`,
  `status_details`, `error_code`, `error_message`;
* all fields bounded and redacted: OCIDs are masked, and `created_by` is
  never returned;
* use the shared SDK page size of 100.

The docstring starts with "AI DP async operations".

### 2. `deploy_aidp_agent` (plan by default)

Signature:
`deploy_aidp_agent(agent_name: str, compute_name: str, *, apply: bool = False, wait: bool = True, timeout_seconds: int = 300)`.

**Plan** (always computed, and the only output when `apply=false`):

1. Resolve the agent by exact name. It must be `type=CODE`. Its entry file
   and, if set, its dependency file must exist in the workspace; otherwise
   raise `AidpError`.
2. Resolve the compute by exact name with the AI Compute-aware lookup. It
   must be `type=AI_COMPUTE` and `state=ACTIVE`. Otherwise raise an
   actionable `AidpError`: suggest `get_cluster_status` and
   `set_cluster_state`, and mention the AI Lakehouse dependency.
3. List the agent's deployments, ignoring `DELETED` ones:
   * no deployment → `action="deploy"`;
   * exactly one PROD deployment in `ACTIVE` state → `action="redeploy"`;
   * any other combination (several PROD deployments, or one in `CREATING`
     or `FAILED`) → `AidpError`, reporting the states found. Never guess.
4. Return:
   * `action`, `agent_name`, `compute_name`;
   * the current deployment summary: `key`, `lifecycle_state`,
     `deployment_type`, `time_created`, `endpoint_url`;
   * notes:
     * a redeploy recreates the deployment;
     * the endpoint URL is expected to stay stable;
     * requests may fail while the redeploy is in progress;
     * a redeploy is required after every code upload.

**Apply** (`apply=true`):

* Use `safety.should_apply`. There is no `unchanged` action: deploying is
  always a real operation.
* `deploy` calls `deploy_agent` with `DeployAgentDetails(agent_key,
  agent_compute_key)`.
* `redeploy` calls `redeploy_agent_by_key` with
  `UpdateAgentDeploymentDetails(agent_key, agent_compute_key)`.
* Pass `retry_strategy=oci.retry.NoneRetryStrategy()`. Never retry a deploy.
* With `wait=true`, poll with bounded intervals (at most every 10 s) until
  `timeout_seconds` (from 30 through 900):
  * **deploy:** exactly one PROD deployment is `ACTIVE`;
  * **redeploy:** exactly one PROD deployment is `ACTIVE` **and its
    `time_created` is later than the plan's `time_created`**. `time_updated`
    and `deployment_version` must not be used: they stay `null`;
  * `FAILED` → stop and report it. Include the newest matching
    `DEPLOY_AGENT` async operation (`error_code`, `error_message`), found
    through `resource_type="AGENT"` and the agent key in the display name.
* Result:
  * `action`, `http_status`;
  * the final deployment summary;
  * `previous_time_created`;
  * `endpoint_stable`, which says whether the URL is equal to the previous
    one;
  * the matching async operation summary;
  * `elapsed_s`, and `timed_out` when applicable.

  With `wait=false`, return right after the request with
  `state="SUBMITTED"`.

Docstring, starting with "AI DP agent deploy": plan by default; `apply=true`
deploys or redeploys on an ACTIVE AI Compute; it uses compute that is already
running; the endpoint can be briefly unavailable during a redeploy; nothing
is deleted.

### 3. Skill `skills/aidp-agent-deploy/`

A user-scope operational skill, installed with `scripts/install_skills.sh`
and validated by `tests/test_skills.py`.

Frontmatter:
* `name: aidp-agent-deploy`;
* `description`: deploy, redeploy, and smoke-test a code-first LangGraph agent
  on OCI AI DP through `aidp-mcp`; not for notebooks, and not for writing the
  agent's code.

`agents/openai.yaml`:
* an MCP dependency on `aidp-mcp`;
* a `default_prompt` that mentions `$aidp-agent-deploy`.

`SKILL.md` body. Keep it concise; put details in `references/`:

1. **Preconditions:**
   * the `aidp-mcp` tools are available; do not fall back to scripts, the
     SDK, the CLI, or REST;
   * absolute local paths are used.
2. **Workflow, with exact tool names:**
   1. run the agent project's local tests; stop if they fail;
   2. `upload_aidp_agent_code` plan → approval → apply;
   3. `ensure_aidp_agent` plan → approval → apply;
   4. `get_cluster_status` on the AI Compute. If it is not ACTIVE, explain the
      cost and ask before `set_cluster_state`;
   5. `deploy_aidp_agent` plan → approval → apply. This step is **mandatory
      after every code upload, even if nothing else changed**;
   6. smoke test: `invoke_aidp_agent` with an agreed, deterministic message,
      after approval (`confirm_invoke=true`). Compare the answer with the
      expected result;
   7. report the endpoint, the session id, and the trace summary.
3. **Authorization:** as in the notebook skill. Every `apply`, `overwrite`,
   `confirm_*`, and start action needs explicit approval, for that action and
   in the current conversation.
4. **Diagnosis:** see `references/troubleshooting.md`, when a step fails.
5. **Stopping conditions:**
   * stop after one failed deploy or smoke test, and report;
   * never retry deploys automatically.

`references/troubleshooting.md` holds the verified facts from this
specification's "Observed live" section. Each fact carries its verification
date, and there is a short "symptom → likely cause → what to do" table:
* AI Compute creation fails with `InternalError` → check that the AI
  Lakehouse is running;
* the endpoint still serves old code → redeploy;
* HTTP 400 `AIDP_USER_CODE_EXECUTION_ERROR` → reproduce locally with the
  contract tests, because the exception text is not exposed remotely;
* session not continued → pass `session_key` (sent as `x-session-id`);
* `get_cluster_status` cannot find a compute → check that it is an AI Compute
  (lookup behavior);
* deployment `FAILED` → `list_aidp_async_operations` with
  `resource_type="AGENT"`.

The skill test must check that every tool name referenced in `SKILL.md` and
in `references/` exists in the MCP snapshot. Extend the existing check from
the notebook skill to all skills under `skills/`.

### Code placement

* Add the deploy logic to a new domain module, `aidp_mcp/agent_deploy.py`,
  that uses only shared modules (`agent_lookup`, `lookups`, `targets`,
  `safety`, `validation`, `workspace_files`). It does not import other agent
  domain modules.
* Put `list_aidp_async_operations` in a small domain module,
  `aidp_mcp/operations_status.py`. Add an `AsyncOperationsClient` context to
  `targets.py`.
* Keep all modules under the ~600-line guideline, with no Pylint disables in
  code or in `pyproject.toml`.

## API design and permissions

* New writes: `deploy_agent` and `redeploy_agent_by_key` on CODE agents. They
  need permission to deploy agents in the workspace.
* New reads: async operations for the instance.
* No compute is created or started by these tools. A deployment consumes
  capacity on an already running AI Compute.
* **Cleanup:** the tools do not undeploy. Undeploying is manual, in the UI.
  Stopping the compute (with `set_cluster_state`) and the AI Lakehouse avoids
  cost after tests.

## Acceptance and verification

Offline tests, with mocked SDK models and network blocked:

* **`list_aidp_async_operations`:**
  * `resource_type` allowlist enforced before any call;
  * status filter;
  * `name_contains` filter;
  * ordering, bounds, and `duration_s`;
  * no `created_by`; OCIDs redacted;
  * JSON-serializable output with real SDK model objects.
* **`deploy_aidp_agent` plan:**
  * deploy versus redeploy selection;
  * errors for a non-CODE agent, missing files, a compute that is missing,
    not an AI Compute, or not ACTIVE, and an ambiguous or failed deployment
    state;
  * no write calls in plan mode.
* **`deploy_aidp_agent` apply:**
  * the exact SDK method and details model per action;
  * `NoneRetryStrategy`;
  * the redeploy wait completes only when `time_created` is newer;
  * a deploy wait completes on `ACTIVE`;
  * a `FAILED` deployment is reported with its async-operation error;
  * timeout reporting;
  * `wait=false` returns `SUBMITTED`;
  * `endpoint_stable` is computed correctly.
* **Skill:**
  * the frontmatter validation passes;
  * every referenced tool exists in the snapshot;
  * `quick_validate.py` passes.
* **MCP snapshot:** updated intentionally with the two new tools. Existing
  tools are byte-identical.
* **Quality gates:**
  * full suite green;
  * Pylint 10.00/10 with no disables;
  * Black clean;
  * `git diff --check` clean.

Manual verification, explicitly authorized, recorded without OCIDs:

1. `list_aidp_async_operations(resource_type="AGENT")` shows the
   `DEPLOY_AGENT` operations of 2026-09-28.
2. On `hello_world_api`, after the demo code change (new response prefix,
   already uploaded):
   * `deploy_aidp_agent` plan → `redeploy` with the current `time_created`;
   * apply → a new `time_created`, `endpoint_stable=true`, and the elapsed
     time recorded.
3. `invoke_aidp_agent` returns the **new** prefix. This proves the redeploy
   serves the uploaded code.
4. Optional: first deployment through the tool, on a fresh CODE agent
   created with `ensure_aidp_agent`. Record the HTTP status, the headers,
   and the time until `ACTIVE`.
5. Skill behavior: in a new Codex session in `agents-4-ai-dp`, ask "update
   the hello world agent on AI DP with the current code and check it works",
   without naming the skill. Record:
   * whether `aidp-agent-deploy` is selected;
   * whether the local tests run first;
   * whether Codex stops for approval before each mutation;
   * whether the redeploy happens after the upload;
   * whether the smoke test uses the expected answer.
6. Cleanup: record the final state, and stop `aicomp02` and the AI Lakehouse
   if no further tests are planned.

## Execution plan

One commit per step. Each step keeps the suite green and Pylint at 10.00/10:

1. The `AsyncOperationsClient` context, and `list_aidp_async_operations` with
   its tool and tests.
2. `deploy_aidp_agent` plan logic, read-only, with its tool and tests.
3. `deploy_aidp_agent` apply and wait, with tests.
4. The `skills/aidp-agent-deploy` skill with `references/troubleshooting.md`,
   and the extended skill test.
5. Documentation:
   * `aidp_mcp/README.md` (tools and workflow);
   * the root `README.md` capability table and roadmap;
   * `skills/README.md`;
   * `CHANGELOG.md`;
   * the final snapshot confirmation.

Finish each step, then commit. Never discard working changes or reset this
file. Stop only on a real blocker, keeping the work in the tree and
describing the blocker in "Verification evidence".

## Verification evidence

### Execution-plan step 1 — 2026-09-28

Implemented the read-only `AsyncOperationsClient` context and
`list_aidp_async_operations` MCP tool. The context resolves only the selected
instance (not a workspace) and retains documented datetime timestamps for
`duration_s`. The tool restricts resource types and statuses before any SDK
call, uses a page size no greater than 100, requests newest-first service
ordering, obtains detail records for bounded error fields, masks OCIDs, and
does not return `created_by`.

Offline verification completed in the `codex-4-oci-aidp` Conda environment:

* `pytest -q` — 273 passed;
* `pylint aidp_common aidp_mcp tests` — 10.00/10;
* Black applied to the changed Python files; `git diff --check` passed.

No live call was made: the manual verification listed above remains pending
explicit authorization and is intentionally outside this read-only
implementation step.

### Execution-plan step 2 — 2026-09-28

Implemented the read-only `deploy_aidp_agent` planner and MCP tool. It
requires an exact CODE agent, verifies its configured entry and optional
dependency files, resolves an exact ACTIVE AI Compute, and selects only a
safe PROD deployment action. The plan ignores deleted deployments and does
not let unrelated TEST deployments alter a valid PROD redeploy decision.
Ambiguous, creating, failed, inactive, or multiply-PROD states stop with the
states found. `apply=true` is rejected locally until step 3, so this commit
cannot submit a deployment.

Offline verification completed in the `codex-4-oci-aidp` Conda environment:

* `pytest -q` — 289 passed;
* `pylint aidp_common aidp_mcp tests` — 10.00/10;
* Black check and `git diff --check` passed.

No live call was made; manual verification remains pending explicit
authorization.

### Execution-plan step 3 — 2026-09-28

Implemented `apply=true` submission and bounded wait for
`deploy_aidp_agent`. The planner is always recomputed before one exact SDK
deploy or redeploy call using `NoneRetryStrategy`; no deploy request is
retried, cancelled, undeployed, or deleted. `wait=false` returns
`SUBMITTED`. A deploy wait needs one ACTIVE PROD deployment, while a redeploy
also needs a strictly newer `time_created`; `time_updated` and deployment
version are not used. Failed deployment states return the newest matching,
sanitized `DEPLOY_AGENT` async-operation error. Timeout leaves the submitted
operation running and reports the last observed state.

Offline verification completed in the `codex-4-oci-aidp` Conda environment:

* `pytest -q` — 293 passed;
* `pylint aidp_common aidp_mcp tests` — 10.00/10;
* Black check and `git diff --check` passed.

No live call was made; the required manual verification remains pending
explicit authorization.

### Execution-plan step 4 — 2026-09-28

Implemented the user-scope `aidp-agent-deploy` operational skill with an
`aidp-mcp` dependency, implicit discovery enabled, a skill-naming default
prompt, and a concise approval-gated workflow. Its troubleshooting reference
records the verified deployment, AI Compute, session, and user-code failure
observations with verification dates. The skill does not author agents or
fall back to SDK, CLI, REST, or scripts.

The skill validation now checks every operational skill's declared MCP tool
contract against the snapshot and scans its Markdown references. Offline
verification completed in the `codex-4-oci-aidp` Conda environment:

* `quick_validate.py skills/aidp-agent-deploy` — `Skill is valid!`;
* `pytest -q` — 293 passed;
* `pylint aidp_common aidp_mcp tests` — 10.00/10;
* Black check and `git diff --check` passed;
* `scripts/install_skills.sh --dry-run` reported creation of both operational
  skills without modifying a user-scope directory.

No live call was made; manual workflow verification remains pending explicit
authorization.

### Execution-plan step 5 — 2026-09-28

Documented the async-operation observation and guarded code-agent deployment
workflow in the MCP guide, including the exact plan, approval, bounded wait,
failure-diagnosis, smoke-test, and stopping boundaries. Updated the project
capability table and roadmap to distinguish offline implementation verification
from pending live deploy/redeploy and invocation verification. Added the two
operational skills and their safety boundaries to the skills guide, and
recorded the user-visible additions in the changelog.

Final MCP snapshot confirmation and offline verification completed in the
`codex-4-oci-aidp` Conda environment:

* `pytest aidp_mcp/tests/test_server.py::test_mcp_tool_contract_matches_snapshot -q`
  — 1 passed; the checked snapshot exactly contains the 22 registered tools,
  including `list_aidp_async_operations` and `deploy_aidp_agent`;
* `pytest -q` — 293 passed;
* `pylint aidp_common aidp_mcp tests` — 10.00/10;
* `black --check aidp_common aidp_mcp tests` — 42 files unchanged;
* `git diff --check` passed.

No live call was made: the manual deploy/redeploy, endpoint-interruption, and
invocation verification required by this specification remain pending explicit
authorization.

### Follow-up fixes — 2026-09-28

Corrected deployment selection and waiting so that only non-deleted PROD
deployments affect a deploy or redeploy. FAILED and CREATING TEST (Playground)
deployments no longer block a valid PROD plan or wait. A failed wait now
requires a PROD deployment from the submitted request's plan baseline; for a
redeploy, its `time_created` must be later than the planned deployment. Older
or non-PROD failures are ignored.

Strengthened operational-skill validation while preserving the explicit
per-skill MCP tool contracts. The test now scans every Markdown file below
`skills/` and rejects every backticked snake_case identifier that contains
`aidp` or starts with a recognized MCP tool prefix when it is absent from the
MCP snapshot. Manual negative verification added a temporary
`deploy_aidp_agnet` reference: the skill test failed and reported the unknown
identifier. The temporary reference was removed and is not committed.

Offline verification completed in the `codex-4-oci-aidp` Conda environment:

* focused deployment and skill tests — 28 passed;
* manual misspelled-tool check — failed as expected, then removed;
* `pytest -q` — 298 passed;
* `pylint aidp_common aidp_mcp tests` — 10.00/10;
* `black --check aidp_common aidp_mcp tests` — 42 files unchanged;
* `git diff --check` passed.

No live call was made; the remote verification items above remain pending
explicit authorization.
