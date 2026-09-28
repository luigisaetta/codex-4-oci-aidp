# AI DP agent observation and invocation MCP tools

## Problem and scope

The first AI DP code-first agent, a deterministic LangGraph hello-world agent
with no LLM call, lives in the separate repository `agents-4-ai-dp`
(`agents/hello_world/`). It will be deployed **manually** from the AI DP
Workbench UI first, for two reasons:

* several platform fields are not documented precisely: `path_info`,
  `entry_file_path`, `compute_key`, the deployment lifecycle, and the source
  of trace keys;
* the invocation contract of the deployed endpoint is documented only
  through examples.

Before building deploy tools (a later specification), Codex needs tools to:

* **observe** agents, deployments, sessions, messages, and traces;
* **invoke** a deployed agent;
* **diagnose** failures.

The manually deployed agent is then the ground truth for designing the write
tools.

Scope:

1. A new domain module `aidp_mcp/agents.py` with read-only observation
   operations, exposed as MCP tools.
2. A guarded `invoke_aidp_agent` tool that sends one message to a deployed
   agent's
   `/chat` endpoint.
3. Tests, documentation, and an intentional update of the MCP tool snapshot.

Non-goals:

* Uploading agent code, creating, updating, validating, deploying,
  redeploying, or deleting agents or deployments (next specification).
* Creating or managing AI Compute.
* Streaming responses, the A2A endpoint, OAuth2-protected deployments, and
  session variables.
* LLM-specific behavior. The hello-world agent has no model; LLM calls and
  IAM policies are phase 2.
* A skill for agents (after the write tools exist).

## Assumptions and prerequisites

Verified by the user on 2026-09-27:

* an AI Compute is available;
* the user can create and deploy agents in the configured workspace;
* the IAM prerequisites are in place.

Verified in the pinned SDK `aidp-python-client` 4.2.1 on 2026-09-27.
`AgentClient` provides:

* `list_agents(ai_data_platform_id, workspace_key, **kwargs)`: kwargs
  `compute_key`, `display_name`, `display_name_contains`, `limit`, `page`,
  `sort_order`, `sort_by`; returns `AgentCollection` of `AgentInfo`;
* `get_agent(..., agent_key)`: returns `Agent`;
* `list_agent_deployments(..., agent_key, **kwargs)`: kwargs include
  `display_name`, `lifecycle_state`, `limit`, `page`, `sort_by`,
  `sort_order`; returns `AgentDeploymentCollection`;
* `get_agent_deployment(..., agent_key, agent_deployment_key)`: returns
  `AgentDeployment`;
* `list_agent_sessions(..., agent_key, **kwargs)`: kwargs include `limit`,
  `page`, `sort_by`, `sort_order`, and time filters; returns
  `AgentSessionCollection` of `AgentSessionSummary`;
* `list_agent_session_chat_histories(..., agent_key, session_id, **kwargs)`:
  kwargs `limit`, `page`; returns `SessionChatHistoryCollection` of
  `SessionChatHistorySummary`;
* `get_agent_session_trace(..., agent_key, session_id, trace_key)`: returns
  `TraceDetails`.

Model fields relevant to this specification:

* `AgentInfo` / `Agent`: `key`, `display_name`, `type`, `path_info`,
  `entry_file_path`, `dependencies_file_path`, `compute_key`,
  `deployment_compute_key`, `deployment_mode`, `uri_state`,
  `lifecycle_state`, `lifecycle_details`, `time_created`, `time_updated`.
* `AgentDeployment`: `key`, `display_name`, `endpoint_url`,
  `deployment_type`, `lifecycle_state`, `deployment_version`,
  `agent_compute_key`, `agent_card_url`, `time_created`, `time_updated`.
* `AgentSessionSummary`: `key`, `display_name`, `lifecycle_state`,
  `endpoint_url`, `time_created`, `time_started`, `time_ended`, `duration`,
  `tokens`.
* `SessionChatHistorySummary`: `key`, `role`, `time_created`, `content`
  (`ChatMessage`: `type`, `text`, …), `tool_name`, `metadata`.
* `TraceDetails`: `trace_id`, `spans` (`SpanDetails`: `span_name`, `kind`,
  `start_time`, `end_time`, `status`, `events`, `attributes`), `start_time`,
  `end_time`.

Documented by Oracle for deployed-agent invocation ([Invoke a deployed
agent](https://docs.oracle.com/en/cloud/paas/ai-data-platform/aidug/invoke-deployed-agent.html),
[Deploy an agent flow](https://docs.oracle.com/en/cloud/paas/ai-data-platform/aidug/agent-flow-deployment.html)).
Re-verify before implementing and record the date:

* endpoint `…/agentendpoint/<agentId>/chat`, stable across redeploys;
* requests signed with OCI request signing, with at least USE permission on
  the agent endpoint;
* the documented Python example uses `requests` with `oci.signer.Signer`;
* request body with `isStreamEnabled`, `input` (a list of
  `{"role": "User", "content": [{"type": "INPUT_TEXT", "text": …}]}`), an
  optional `sessionKey`, and an optional `metadata` object;
* response with `output[].content[].text`.

Not documented or not yet known. **Discover these with the manually deployed
agent and record them in "Verification evidence":**

* the meaning and format of `path_info` and `entry_file_path`;
* where a trace key comes from (chat-history `metadata`, response headers, or
  another field);
* the exact `/chat` response schema, including where the session key is
  returned;
* whether `get_cluster_status` finds an AI Compute by name.

## Interfaces and behavior

### Module and clients

* Add `aidp_mcp/agents.py` as a domain module, following the dependency rules
  of specification 009. It imports only shared modules and `aidp_common`.
* Add an `AgentClient` context to `targets.py`, for example
  `agent_clients(settings)`, with the same timeout, retry, redirect, and
  timestamp handling as the existing Workbench clients.
* Resolve an agent by **exact, case-sensitive display name** in the
  configured workspace. Zero matches or more than one match raise
  `AidpError` with an actionable message. Use `list_agents` with
  `display_name` and verify exact equality locally.
* Paginate with the shared `lookups.next_page`. Bound every listing locally.
* Rename `lookups._resource_key` to `resource_key`. It is shared API across
  modules (a nit from the specification 009 review). Update all callers.

### Read-only tools

All are read-only and return sanitized, bounded metadata. Never return raw
SDK objects.

| Tool | Parameters | Returns |
| --- | --- | --- |
| `list_aidp_agents` | `name_contains: str \| None = None`, `max_results: int = 50` | Agents with `name`, `key`, `type`, `lifecycle_state`, `lifecycle_details`, `deployment_mode`, `uri_state`, `entry_file_path`, `dependencies_file_path`, `path_info`, and whether a compute is attached; plus `truncated` |
| `get_aidp_agent` | `agent_name: str` | The same agent fields, plus its deployments: `key`, `lifecycle_state`, `deployment_type`, `deployment_version`, `endpoint_url`, `time_created`, `time_updated` |
| `list_aidp_agent_sessions` | `agent_name: str`, `max_results: int = 25` | Newest-first sessions: `session_id`, `display_name`, `lifecycle_state`, timestamps, `duration`, `tokens`; plus `truncated` |
| `get_aidp_agent_session_messages` | `agent_name: str`, `session_id: str`, `max_characters: int = 12000` | Ordered messages: `role`, `time_created`, `tool_name`, and text content bounded in total by `max_characters`; `metadata` **keys only**; plus `truncated`. The docstring states that messages can contain application data and should be requested only when authorized, like `get_job_run_output` |
| `get_aidp_agent_trace` | `agent_name: str`, `session_id: str`, `trace_key: str`, `max_spans: int = 100` | `trace_id`, total `duration_ms`, and spans in start order, each with `span_name`, normalized `kind`, JSON-safe `status` (`code`, bounded `message`), `duration_ms`, and **error events only** (event name and a bounded message). Span `attributes` are **not** returned, because they can contain prompts and data |

Validation:

* validate `session_id` and `trace_key` as single path segments with
  `aidp_common.connection.validate_resource_key`;
* validate the bounds with `validation._validate_result_limit`, or its
  renamed public equivalent. Maximums: 1,000 results; 100,000 characters for
  messages; 1,000 spans.

### `invoke_aidp_agent`

Signature:
`invoke_aidp_agent(agent_name: str, message: str, *, session_key: str | None = None, timeout_seconds: int = 120, max_characters: int = 12000, confirm_invoke: bool = False)`.

Safety:

* Invocation creates a session. It may consume compute and, for future
  agents, call models or tools with side effects. It therefore requires
  `confirm_invoke=true`, checked with `safety.require_confirmation`
  **before** any remote call. Message: "Set confirm_invoke=true to send a
  message to a deployed agent."
* Take the endpoint from the agent's **ACTIVE** deployment `endpoint_url`
  reported by the SDK. Never let the caller supply a URL or host. Fail if
  there is no active deployment, or if more than one exists, naming the
  states found.
* Validate the endpoint before sending:
  * scheme `https`;
  * a host ending in `.oraclecloud.com`;
  * no credentials, query, or fragment in the URL.

  Use an endpoint path already ending in `/chat` unchanged. If the path ends
  in `/agentendpoint/<agent-key>`, append exactly `/chat`. Reject every other
  path, including an A2A path, with an actionable error. Disable redirects.
* `message` must be nonempty and at most 20,000 characters. Validate
  `session_key` as a single path segment when given.
* Bound `timeout_seconds` to between 1 and 600.

Transport:

* Follow the documented Oracle example: `requests` with an OCI signer built
  from the same profile and signer options as `aidp_common.load_auth`. Add
  `requests` as a direct, pinned runtime dependency in `requirements.txt`
  and `pyproject.toml`, with a comment linking the documentation. Keep the
  pin-consistency test green.
* Do not use `oci._vendor` or other private modules.
* Request body:
  `{"isStreamEnabled": false, "input": [{"role": "User", "content": [{"type": "INPUT_TEXT", "text": message}]}]}`,
  plus `"sessionKey": session_key` when given. No `metadata` in this
  specification.

Response:

* On HTTP 2xx, return:
  * `agent_name` and `http_status`;
  * `response_id`, if present;
  * `session_key`, from the response body or headers once discovered;
    otherwise `null` with a note;
  * `text`: the concatenated `output[].content[]` items whose type is exactly
    `output_text`, bounded by `max_characters`, plus `truncated`. Ignore
    serialized `trace` content items;
  * `trace_id` from `output_text.traces.id`, and `session_id` and
    `session_key` from `output_text.traces.parentSessionId`;
  * `trace_summary`: at most 1,000 sanitized trace spans with `span_name`, a
    normalized kind name, bounded `status` (`code`, `message`), and
    nanosecond-derived `duration_ms`; exclude attributes and non-error events;
  * `usage` with `input_tokens`, `output_tokens`, and `total_tokens` from the
    response token counts; and a bounded `agent_error` (`code`, `message`)
    only when the response `error.code` is nonempty;
  * `response_keys`: the top-level keys of the JSON body, so the unknown
    schema can be observed without dumping it.
* On a non-2xx status, raise `AidpError` with the status code and a bounded,
  sanitized excerpt of the body (at most 1,000 characters). Never include
  request headers or signatures.
* On timeout or connection error, raise `AidpError` with an actionable
  message. Do not retry.

### Tool docstrings

The docstrings are what the model reads. For each new tool, state:

* read-only or confirmation-gated;
* exact-name matching;
* bounds;
* for `invoke_aidp_agent`, that a session is created and the costs involved.

## API design and permissions

* Read tools use `AgentClient` read operations. They need read access to the
  workspace's agents, sessions, and traces.
* `invoke_aidp_agent` needs USE permission on the agent endpoint (documented). No
  create, update, or delete operation is introduced.
* No new OCI or AI DP resource is created by the tools, except the session
  that the platform creates for each invocation.

## Acceptance and verification

Offline tests in `aidp_mcp/tests/test_agents.py` (and `test_targets.py` for
the new client context), with mocked SDK clients and a mocked HTTP session.
Network stays blocked:

* exact-name resolution: zero or duplicate matches raise `AidpError`; a
  case-only match is not accepted;
* listings paginate, are bounded, and report `truncated`;
* messages: text is bounded in total, and only metadata keys are returned;
* traces: spans are ordered, span attributes are absent from the output, and
  only error events are kept, with bounded messages;
* `invoke_aidp_agent`:
  * without `confirm_invoke=true` it raises before any SDK or HTTP call;
  * it fails with no active deployment or several active ones;
  * it rejects endpoints that are not https, have a non-Oracle host, or
    contain credentials, a query, or a fragment;
  * it sends exactly the documented body, to `…/chat`, with redirects
    disabled and the configured timeout;
  * on non-2xx it raises with a bounded excerpt, and no header or signature
    appears;
  * on timeout it raises without retrying;
  * text extraction returns only `output_text` content, excluding serialized
    trace content; it is bounded;
  * the observed `output_text.traces` returns `trace_id`, `session_id`, and a
    bounded span-name/kind/status/`duration_ms` summary with no attributes;
  * the verified top-level `usage` token counts and a nonempty `error.code`
    produce sanitized `usage` and `agent_error` results.
* All six agent tools run with representative generated SDK model objects and
  their results pass `json.dumps`; SDK model objects must never leak into MCP
  tool output.
* Cluster status and lifecycle lookup also query `list_clusters` with
  `type="AI_COMPUTE"`, retaining exact-name matching and duplicate detection.
* The MCP tool snapshot is updated **intentionally** with the six new tools,
  and existing tool entries are byte-identical. The evidence lists the added
  tool names.
* Quality gates: full suite green, Pylint 10.00/10 with no disables, Black
  clean, the pin-consistency test green with `requests`, and
  `git diff --check` clean.

Manual verification, explicit and recorded without OCIDs:

1. The user deploys `agents-4-ai-dp/agents/hello_world` from the Workbench
   UI (code mode, entry file `hello_agent.py`, dependency file
   `requirements.txt`, attached AI Compute), then deploys it.
2. In a new Codex session:
   * `list_aidp_agents` and `get_aidp_agent`: record the observed `path_info`,
     `entry_file_path`, `dependencies_file_path`, `deployment_mode`, and the
     deployment `lifecycle_state` and `deployment_type`;
   * `get_cluster_status` on the AI Compute name: record whether it works.
3. `invoke_aidp_agent` with `message="Hi there"` and `confirm_invoke=true`.
   * Expected text: `hello world - you said: Hi there`.
   * Record `response_keys` and where the session key was found.
4. A second `invoke_aidp_agent` call with the returned `session_key`: record
   whether the session is reused.
5. `list_aidp_agent_sessions`, `get_aidp_agent_session_messages`, and, if a
   trace key can be found, `get_aidp_agent_trace`: record where the trace key
   came from.
6. Error path: deploy a variant whose `invoke()` raises. Record what
   `invoke_aidp_agent` returns and what the messages and trace show.

   Revert afterwards, so that the deployed agent is the correct hello world
   again.

Update this specification's "Verification evidence" with the observed facts.
These facts are the input for the write-tools specification.

## Execution plan

One commit per step. Every step keeps the suite green and Pylint at 10.00/10:

1. The `agent_clients` context in `targets.py`, the `resource_key` rename,
   and `list_agents` and `get_agent` in `agents.py`, with their tools and
   tests.
2. `list_agent_sessions`, `get_agent_session_messages`, and
   `get_agent_trace`, with their tools and tests.
3. `invoke_agent`, the `requests` dependency, and its tests.
4. Documentation (`aidp_mcp/README.md` tool table and code layout,
   `CHANGELOG.md`) and the final snapshot confirmation.

Finish each step, then commit. Never discard working changes or reset this
file. Stop only on a real blocker, keeping the work in the tree and
describing the blocker in "Verification evidence".

## Verification evidence

2026-09-27, execution-plan step 1:

* Re-verified Oracle's deployed-agent invocation and deployment documentation.
  It still documents OCI authentication with at least USE permission, Python
  `requests` with an OCI signer, and a stable agent-specific `/chat` endpoint.
* Offline verification passed: 204 tests, Black clean, Pylint 10.00/10, and
  `git diff --check` clean. The MCP contract snapshot intentionally adds only
  `list_agents` and `get_agent`; all existing entries are byte-identical.
* Manual AI DP verification remains pending. No OCI resource was created or
  modified by this step.

2026-09-27, execution-plan step 2:

* Offline verification passed for bounded newest-first sessions, bounded
  session-message text with metadata keys only, and ordered trace spans with
  attributes omitted and bounded error events only.
* The MCP contract snapshot intentionally adds `list_agent_sessions`,
  `get_agent_session_messages`, and `get_agent_trace`; previously committed
  entries are byte-identical.
* Manual AI DP verification remains pending. No OCI resource was created or
  modified by this step.

2026-09-27, live observation and follow-up correction:

* In Codex, the original MCP names `list_agents` and `get_agent` were
  confusable with Codex built-in agent tools. The MCP adapter now exposes the
  observation tools as `list_aidp_agents`, `get_aidp_agent`,
  `list_aidp_agent_sessions`, `get_aidp_agent_session_messages`, and
  `get_aidp_agent_trace`; the planned step-3 tool name is
  `invoke_aidp_agent`.
* AI DP live result: `AgentClient.list_agents` returned HTTP 200 with
  `limit=100`, while `limit=500` and `limit=1000` returned HTTP 500
  `InternalError`. A shared SDK page size of 100 is now used for all paginated
  AgentClient lists and other domain list requests that can otherwise exceed
  it; local result bounds remain unchanged.
* For undeployed `hello_world`, `list_aidp_agent_sessions` returned HTTP 500
  `InternalError` with every parameter combination tried. Re-check this after
  deployment.
* `hello_world` was observed as type `CODE`, lifecycle `DRAFT`, not deployed,
  with entry file path `/Workspace/hello_world/hello_agent.py` and zero
  deployments. No OCIDs are recorded.
* Offline verification for this correction is recorded with the implementation
  change. The MCP snapshot intentionally renames only the five agent tool
  entries and their descriptions; non-agent tool entries are byte-identical.

2026-09-27, live `get_aidp_agent` observation for the UI-created
`hello_world` agent:

* The read-only `get_aidp_agent` MCP tool reported type `CODE`, lifecycle
  `DRAFT`, and deployment mode `NOT_DEPLOYED`.
* `entry_file_path` was `/Workspace/hello_world/hello_agent.py` and
  `dependencies_file_path` was `/Workspace/hello_world/requirements.txt`.
  `path_info` was `/Workspace`, the workspace root, although the SDK describes
  that field as a volume path.
* No compute was attached and the deployment list was empty. No keys or OCIDs
  are recorded.

2026-09-27, execution-plan step 3:

* Re-verified Oracle's deployed-agent invocation and deployment documentation.
  It documents OCI request signing with `requests`, a non-streaming chat body,
  an endpoint-specific `/chat` path, and at least USE permission on the agent
  endpoint.
* Offline verification passed: 222 tests, Black clean, Pylint 10.00/10, and
  `git diff --check` clean. The MCP contract snapshot intentionally adds only
  `invoke_aidp_agent`; existing entries are byte-identical.
* Manual AI DP invocation remains pending. It was not run during implementation
  because it creates a session and can consume compute or trigger agent tools.

2026-09-27, execution-plan step 4:

* Updated `aidp_mcp/README.md` with the confirmation-gated
  `invoke_aidp_agent` tool, its session and cost boundary, and the
  `agents.py` responsibility for OCI-signed invocation.
* Updated `CHANGELOG.md` under `Unreleased`. The final MCP snapshot contains
  18 tools; `invoke_aidp_agent` is the only added entry and pre-existing
  snapshot entries remain byte-identical.
* The documented manual AI DP checks remain pending: a human must deploy the
  hello-world agent and explicitly authorize each session-creating invocation.

2026-09-27, invocation-excerpt redaction follow-up:

* Offline tests confirm that a non-2xx invocation excerpt replaces a matching
  OCID with `<ocid>` before applying its 1,000-character bound, while an
  excerpt without an OCID is unchanged.
* The `invoke_aidp_agent` tool description now starts with `AI DP agent`, like
  the other agent tools. The MCP snapshot was intentionally updated only for
  that description; its schema and all other entries are unchanged.
* No AI DP invocation was made for this follow-up; the result is locally
  verified with mocked SDK and HTTP clients.

2026-09-28, live deployment finding and chat URL correction:

* The deployed agent reported `lifecycle_state` `DEPLOYED`,
  `deployment_mode` `DEPLOYED`, and `uri_state` `ACTIVE`. Its sole deployment
  was `ACTIVE`, had `deployment_type` `PROD`, `deployment_version` `null`, and
  an `endpoint_url` ending in `/chat`. The URL's `agentId` matched the agent
  key. No OCIDs are recorded.
* Attaching compute and testing in the Playground changed only
  `compute_attached`; the observed deployment fields above were unchanged.
* The live endpoint shape is
  `https://gateway.aidp.eu-frankfurt-1.oci.oraclecloud.com/agentendpoint/<agent-key>/chat`.
  Invocation now preserves that supported path rather than appending a second
  `/chat`; it appends `/chat` only to a validated
  `/agentendpoint/<agent-key>` path and rejects unsupported paths such as
  `/a2a`.
* Local verification passed: the full suite passed (259 tests), Black was
  clean, Pylint scored 10.00/10, and `git diff --check` was clean. The new
  offline coverage verifies the live `/agentendpoint/<agent-key>/chat` shape,
  a base agent endpoint that needs `/chat`, and rejection of `/a2a`.

2026-09-28, live end-to-end invocation and serialization follow-up:

* The first end-to-end invocation returned HTTP 200 and the expected hello-world
  agent text. The response exposed `output` and its inline trace together;
  `output[].content[]` contained the agent answer plus the trace rather than a
  standalone response field.
* The observed trace spans were `agent_invoke > LangGraph.workflow >
  respond.task`. `trace.id` matched the trace key and `trace.parentSessionId`
  was the session identifier accepted by the session observation tools.
  `list_aidp_agent_sessions` and `get_aidp_agent_session_messages` now work on
  the deployed agent. No identifiers, response bodies, or agent data are
  recorded here.
* Live trace serialization exposed an MCP contract bug: span `status` was a
  generated SDK `SpanStatus` object, kind was numeric (`1` for `INTERNAL`),
  and timestamp differences were nanoseconds. This follow-up normalizes these
  fields to JSON-safe status mappings, span-kind names where mapped, and float
  `duration_ms` values. It also returns only agent-answer text and a bounded,
  attribute-free inline trace summary, and uses the discovered session ID as
  `session_key` with a note only when no session can be found.
* This evidence is from one deployed-agent run on OCI AI DP. It verifies the
  observed response shape, not every possible agent or trace shape. Offline
  tests use sanitized representative SDK models and responses; final local
  quality-gate results are recorded with this implementation change.

2026-09-28, manual test C parser-regression correction:

* Manual test C returned HTTP 200 but exposed an empty parsed text and null
  trace/session fields. The verified sanitized `/chat` shape is top-level
  `object`, `model`, `error`, `usage`, and `metadata`, with
  `output[0] = {type: "message", role: "assistant", content: [...]}`.
  Its first content item is `{type: "output_text", text, traces}` and its
  second is `{type: "trace", text}` containing serialized trace data.
* `traces` provides `id`, `parentSessionId`, timestamps, resources, and spans;
  each span provides IDs, `spanName`, kind, status, timestamps, attributes,
  and events. The invocation parser now reads only `output_text`, takes the
  trace and session identifiers from `traces`, and never returns resources,
  attributes, or non-error events. It also exposes only the three token counts
  and a bounded response error when its code is nonempty.
* The exact sanitized shape is covered offline, including an assertion that no
  span attribute reaches the result. Local quality-gate results are recorded
  after this correction: 263 tests passed, Black was clean, Pylint scored
  10.00/10, and `git diff --check` was clean.
