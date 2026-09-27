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
2. A guarded `invoke_agent` tool that sends one message to a deployed agent's
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
| `list_agents` | `name_contains: str \| None = None`, `max_results: int = 50` | Agents with `name`, `key`, `type`, `lifecycle_state`, `lifecycle_details`, `deployment_mode`, `uri_state`, `entry_file_path`, `dependencies_file_path`, `path_info`, and whether a compute is attached; plus `truncated` |
| `get_agent` | `agent_name: str` | The same agent fields, plus its deployments: `key`, `lifecycle_state`, `deployment_type`, `deployment_version`, `endpoint_url`, `time_created`, `time_updated` |
| `list_agent_sessions` | `agent_name: str`, `max_results: int = 25` | Newest-first sessions: `session_id`, `display_name`, `lifecycle_state`, timestamps, `duration`, `tokens`; plus `truncated` |
| `get_agent_session_messages` | `agent_name: str`, `session_id: str`, `max_characters: int = 12000` | Ordered messages: `role`, `time_created`, `tool_name`, and text content bounded in total by `max_characters`; `metadata` **keys only**; plus `truncated`. The docstring states that messages can contain application data and should be requested only when authorized, like `get_job_run_output` |
| `get_agent_trace` | `agent_name: str`, `session_id: str`, `trace_key: str`, `max_spans: int = 100` | `trace_id`, total duration, and spans in start order, each with `span_name`, `kind`, `status`, duration, and **error events only** (event name and a bounded message). Span `attributes` are **not** returned, because they can contain prompts and data |

Validation:

* validate `session_id` and `trace_key` as single path segments with
  `aidp_common.connection.validate_resource_key`;
* validate the bounds with `validation._validate_result_limit`, or its
  renamed public equivalent. Maximums: 1,000 results; 100,000 characters for
  messages; 1,000 spans.

### `invoke_agent`

Signature:
`invoke_agent(agent_name: str, message: str, *, session_key: str | None = None, timeout_seconds: int = 120, max_characters: int = 12000, confirm_invoke: bool = False)`.

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

  Append exactly `/chat`, handling a trailing slash. Disable redirects.
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
  * `text`: the concatenated `output[].content[].text`, bounded by
    `max_characters`, plus `truncated`;
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
* for `invoke_agent`, that a session is created and the costs involved.

## API design and permissions

* Read tools use `AgentClient` read operations. They need read access to the
  workspace's agents, sessions, and traces.
* `invoke_agent` needs USE permission on the agent endpoint (documented). No
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
* `invoke_agent`:
  * without `confirm_invoke=true` it raises before any SDK or HTTP call;
  * it fails with no active deployment or several active ones;
  * it rejects endpoints that are not https, have a non-Oracle host, or
    contain credentials, a query, or a fragment;
  * it sends exactly the documented body, to `…/chat`, with redirects
    disabled and the configured timeout;
  * on non-2xx it raises with a bounded excerpt, and no header or signature
    appears;
  * on timeout it raises without retrying;
  * text extraction concatenates `output[].content[].text` and is bounded.
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
   * `list_agents` and `get_agent`: record the observed `path_info`,
     `entry_file_path`, `dependencies_file_path`, `deployment_mode`, and the
     deployment `lifecycle_state` and `deployment_type`;
   * `get_cluster_status` on the AI Compute name: record whether it works.
3. `invoke_agent` with `message="Hi there"` and `confirm_invoke=true`.
   * Expected text: `hello world - you said: Hi there`.
   * Record `response_keys` and where the session key was found.
4. A second `invoke_agent` call with the returned `session_key`: record
   whether the session is reused.
5. `list_agent_sessions`, `get_agent_session_messages`, and, if a trace key
   can be found, `get_agent_trace`: record where the trace key came from.
6. Error path: deploy a variant whose `invoke()` raises. Record what
   `invoke_agent` returns and what the messages and trace show.

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

Pending implementation.
