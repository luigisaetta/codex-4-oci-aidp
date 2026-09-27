"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Offline tests for AI DP MCP agent observation operations.
"""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aidp_common.connection import AidpError
from aidp_mcp import agents


@contextmanager
def _clients(client):
    """Provide a minimal agent-client context for one offline test."""
    yield "instance", "workspace", client


@contextmanager
def _workspace_clients(objects):
    """Provide the workspace-object client contract for agent upload tests."""
    yield "instance", "workspace", Mock(), Mock(), Mock(), objects


def _agent(name="hello", key="agent-key", **values):
    """Build a representative AgentInfo SDK summary fixture."""
    defaults = {
        "display_name": name,
        "key": key,
        "type": "CODE",
        "lifecycle_state": "ACTIVE",
        "lifecycle_details": None,
        "deployment_mode": "MANUAL",
        "uri_state": "READY",
        "entry_file_path": "hello_agent.py",
        "dependencies_file_path": "requirements.txt",
        "path_info": "agents/hello",
        "compute_key": None,
        "deployment_compute_key": None,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


def _response(items, page=None):
    """Build an OCI-like collection response with an optional next-page token."""
    return SimpleNamespace(
        data=SimpleNamespace(items=items),
        headers={} if page is None else {"opc-next-page": page},
    )


def test_list_agents_paginates_bounds_and_sanitizes_results(monkeypatch):
    """Agent listing returns only the selected fields up to the caller bound."""
    client = Mock()
    client.list_agents.side_effect = [
        _response([_agent("first"), _agent("second")], "later"),
        _response([_agent("third"), _agent("fourth")]),
    ]
    monkeypatch.setattr(agents, "agent_clients", lambda _settings: _clients(client))

    result = agents.list_agents(SimpleNamespace(), "agent", 3)

    assert [item["name"] for item in result["agents"]] == [
        "first",
        "second",
        "third",
    ]
    assert result["truncated"] is True
    assert result["agents"][0]["compute_attached"] is False
    assert set(result["agents"][0]) == {
        "name",
        "key",
        "type",
        "lifecycle_state",
        "lifecycle_details",
        "deployment_mode",
        "uri_state",
        "entry_file_path",
        "dependencies_file_path",
        "path_info",
        "compute_attached",
    }
    assert client.list_agents.call_args_list[0].kwargs == {
        "display_name_contains": "agent",
        "limit": 3,
        "page": None,
    }
    assert client.list_agents.call_args_list[1].kwargs["page"] == "later"


@pytest.mark.parametrize("name", ["missing", "duplicate", "HELLO"])
def test_get_agent_requires_one_exact_case_sensitive_match(monkeypatch, name):
    """Zero, duplicate, and case-only matches never select an agent."""
    client = Mock()
    matches = {
        "missing": [],
        "duplicate": [_agent("duplicate", "one"), _agent("duplicate", "two")],
        "HELLO": [_agent("hello")],
    }
    client.list_agents.return_value = _response(matches[name])
    monkeypatch.setattr(agents, "agent_clients", lambda _settings: _clients(client))

    with pytest.raises(AidpError, match="visible exact matches"):
        agents.get_agent(SimpleNamespace(), name)

    client.get_agent.assert_not_called()


def test_get_agent_returns_bounded_deployments(monkeypatch):
    """Exact agent reads retrieve details and safely bounded deployment metadata."""
    client = Mock()
    client.list_agents.return_value = _response([_agent(compute_key="compute")])
    client.get_agent.return_value = SimpleNamespace(data=_agent(compute_key="compute"))
    deployment = SimpleNamespace(
        key="deployment-key",
        lifecycle_state="ACTIVE",
        deployment_type="AIDP",
        deployment_version="1",
        endpoint_url="https://gateway.aidp.example.oraclecloud.com/agentendpoint/id",
        time_created=1,
        time_updated=2,
    )
    client.list_agent_deployments.return_value = _response([deployment])
    monkeypatch.setattr(agents, "agent_clients", lambda _settings: _clients(client))

    result = agents.get_agent(SimpleNamespace(), "hello")

    assert result["name"] == "hello"
    assert result["compute_attached"] is True
    assert result["deployments"] == [
        {
            "key": "deployment-key",
            "lifecycle_state": "ACTIVE",
            "deployment_type": "AIDP",
            "deployment_version": "1",
            "endpoint_url": deployment.endpoint_url,
            "time_created": 1,
            "time_updated": 2,
        }
    ]
    assert result["deployments_truncated"] is False
    assert all(
        call.kwargs["limit"] <= 100
        for call in client.list_agents.call_args_list
        + client.list_agent_deployments.call_args_list
    )


def _resolved_client(agent=None):
    """Return a client whose exact-name lookup resolves one agent."""
    client = Mock()
    client.list_agents.return_value = _response([agent or _agent()])
    return client


def test_list_agent_sessions_paginates_bounds_and_orders_newest_first(monkeypatch):
    """Session listing uses the documented newest-first SDK sort and local bound."""
    client = _resolved_client()
    first = SimpleNamespace(
        key="first-session",
        display_name="first",
        lifecycle_state="ACTIVE",
        time_created=4,
        time_started=5,
        time_ended=None,
        duration=3,
        tokens=8,
    )
    second = SimpleNamespace(
        key="second-session",
        display_name="second",
        lifecycle_state="ENDED",
        time_created=3,
        time_started=3,
        time_ended=4,
        duration=1,
        tokens=2,
    )
    client.list_agent_sessions.return_value = _response([first, second])
    monkeypatch.setattr(agents, "agent_clients", lambda _settings: _clients(client))

    result = agents.list_agent_sessions(SimpleNamespace(), "hello", 1)

    assert result == {
        "sessions": [
            {
                "session_id": "first-session",
                "display_name": "first",
                "lifecycle_state": "ACTIVE",
                "time_created": 4,
                "time_started": 5,
                "time_ended": None,
                "duration": 3,
                "tokens": 8,
            }
        ],
        "truncated": True,
    }
    assert client.list_agent_sessions.call_args.kwargs == {
        "limit": 1,
        "page": None,
        "sort_by": "timeCreated",
        "sort_order": "DESC",
    }


def test_get_agent_session_messages_bounds_text_and_hides_metadata_values(monkeypatch):
    """Message reads preserve order while exposing bounded text and metadata keys."""
    client = _resolved_client()
    client.list_agent_session_chat_histories.return_value = _response(
        [
            SimpleNamespace(
                role="User",
                time_created=1,
                tool_name=None,
                content=SimpleNamespace(text="hello"),
                metadata={"traceKey": "private", "session": "also-private"},
            ),
            SimpleNamespace(
                role="Assistant",
                time_created=2,
                tool_name="lookup",
                content=[SimpleNamespace(text=" world"), SimpleNamespace(text="!")],
                metadata=None,
            ),
        ]
    )
    monkeypatch.setattr(agents, "agent_clients", lambda _settings: _clients(client))

    result = agents.get_agent_session_messages(
        SimpleNamespace(), "hello", "session-id", 7
    )

    assert result == {
        "session_id": "session-id",
        "messages": [
            {
                "role": "User",
                "time_created": 1,
                "tool_name": None,
                "text": "hello",
                "metadata_keys": ["session", "traceKey"],
            },
            {
                "role": "Assistant",
                "time_created": 2,
                "tool_name": "lookup",
                "text": " w",
                "metadata_keys": [],
            },
        ],
        "truncated": True,
    }
    client.list_agent_session_chat_histories.assert_called_once_with(
        "instance", "workspace", "agent-key", "session-id", limit=100, page=None
    )


def test_agent_client_lists_never_exceed_the_sdk_page_size(monkeypatch):
    """Every AgentClient listing caps each remote request at 100 results."""
    client = _resolved_client()
    client.get_agent.return_value = SimpleNamespace(data=_agent())
    client.list_agent_deployments.return_value = _response([])
    client.list_agent_sessions.return_value = _response([])
    client.list_agent_session_chat_histories.return_value = _response([])
    monkeypatch.setattr(agents, "agent_clients", lambda _settings: _clients(client))

    agents.list_agents(SimpleNamespace(), max_results=1000)
    agents.get_agent(SimpleNamespace(), "hello")
    agents.list_agent_sessions(SimpleNamespace(), "hello", max_results=1000)
    agents.get_agent_session_messages(
        SimpleNamespace(), "hello", "session-id", max_characters=100000
    )

    list_calls = (
        client.list_agents.call_args_list
        + client.list_agent_deployments.call_args_list
        + client.list_agent_sessions.call_args_list
        + client.list_agent_session_chat_histories.call_args_list
    )
    assert list_calls
    assert all(call.kwargs["limit"] <= 100 for call in list_calls)


@pytest.mark.parametrize("session_id, trace_key", [("bad/path", "trace"), ("ok", "..")])
def test_get_agent_trace_validates_path_segments_before_client_creation(
    monkeypatch, session_id, trace_key
):
    """Unsafe session and trace keys are rejected before a remote lookup."""
    client_context = Mock()
    monkeypatch.setattr(agents, "agent_clients", client_context)

    with pytest.raises(AidpError, match="single path segments"):
        agents.get_agent_trace(SimpleNamespace(), "hello", session_id, trace_key)

    client_context.assert_not_called()


def test_get_agent_trace_orders_spans_and_returns_only_bounded_error_events(
    monkeypatch,
):
    """Trace output excludes span attributes and non-error events by design."""
    client = _resolved_client()
    later = SimpleNamespace(
        span_name="later",
        kind="INTERNAL",
        status="OK",
        start_time=20,
        end_time=24,
        attributes={"prompt": "secret"},
        events=[SimpleNamespace(name="progress", attributes={"message": "skip"})],
    )
    earlier = SimpleNamespace(
        span_name="earlier",
        kind="CLIENT",
        status="ERROR",
        start_time=10,
        end_time=15,
        attributes={"prompt": "secret"},
        events=[
            SimpleNamespace(
                name="exception",
                attributes={"exception.message": "x" * 1200, "secret": "no"},
            )
        ],
    )
    client.get_agent_session_trace.return_value = SimpleNamespace(
        data=SimpleNamespace(
            trace_id="trace-id", start_time=10, end_time=24, spans=[later, earlier]
        )
    )
    monkeypatch.setattr(agents, "agent_clients", lambda _settings: _clients(client))

    result = agents.get_agent_trace(
        SimpleNamespace(), "hello", "session-id", "trace-id", 2
    )

    assert result["trace_id"] == "trace-id"
    assert result["duration"] == 14
    assert [span["span_name"] for span in result["spans"]] == ["earlier", "later"]
    assert "attributes" not in result["spans"][0]
    assert result["spans"][0]["error_events"] == [
        {"name": "exception", "message": "x" * 1000}
    ]
    assert result["spans"][1]["error_events"] == []


def _invoke_client(deployments):
    """Return an exact-name agent client with chosen deployment summaries."""
    client = _resolved_client()
    client.list_agent_deployments.return_value = _response(deployments)
    return client


def _deployment(
    state="ACTIVE",
    endpoint="https://gateway.aidp.example.oraclecloud.com/agentendpoint/id",
):
    """Build a minimal deployment fixture for invocation tests."""
    return SimpleNamespace(
        key="deployment-key",
        lifecycle_state=state,
        deployment_type="AIDP",
        deployment_version="1",
        endpoint_url=endpoint,
        time_created=None,
        time_updated=None,
    )


def _successful_response(body=None, headers=None):
    """Build a minimal successful HTTP response fixture."""
    response = Mock(status_code=200, headers=headers or {}, text="")
    response.json.return_value = body or {}
    return response


def test_invoke_agent_requires_confirmation_before_any_remote_call(monkeypatch):
    """An omitted confirmation cannot create clients, sessions, or HTTP calls."""
    client_context = Mock()
    http_call = Mock()
    monkeypatch.setattr(agents, "agent_clients", client_context)
    monkeypatch.setattr(agents, "_post_agent_message", http_call)

    with pytest.raises(AidpError, match="confirm_invoke=true"):
        agents.invoke_agent(SimpleNamespace(), "hello", "Hi there")

    client_context.assert_not_called()
    http_call.assert_not_called()


@pytest.mark.parametrize(
    "deployments, error",
    [([], "found 0"), ([_deployment(), _deployment()], "found 2")],
)
def test_invoke_agent_requires_exactly_one_active_deployment(
    monkeypatch, deployments, error
):
    """No deployment or multiple active deployments never send an HTTP request."""
    client = _invoke_client(deployments)
    http_call = Mock()
    monkeypatch.setattr(agents, "agent_clients", lambda _settings: _clients(client))
    monkeypatch.setattr(agents, "_post_agent_message", http_call)

    with pytest.raises(AidpError, match=error):
        agents.invoke_agent(SimpleNamespace(), "hello", "Hi there", confirm_invoke=True)

    http_call.assert_not_called()


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://gateway.aidp.example.oraclecloud.com/agentendpoint/id",
        "https://gateway.example.com/agentendpoint/id",
        "https://user:password@gateway.aidp.example.oraclecloud.com/id",
        "https://gateway.aidp.example.oraclecloud.com/id?query=value",
        "https://gateway.aidp.example.oraclecloud.com/id#fragment",
    ],
)
def test_invoke_agent_rejects_unsafe_deployment_endpoints(monkeypatch, endpoint):
    """Only a clean Oracle HTTPS deployment endpoint may be used."""
    client = _invoke_client([_deployment(endpoint=endpoint)])
    http_call = Mock()
    monkeypatch.setattr(agents, "agent_clients", lambda _settings: _clients(client))
    monkeypatch.setattr(agents, "_post_agent_message", http_call)

    with pytest.raises(AidpError, match="invalid Oracle HTTPS"):
        agents.invoke_agent(SimpleNamespace(), "hello", "Hi there", confirm_invoke=True)

    http_call.assert_not_called()


def test_invoke_agent_sends_documented_request_without_redirects(monkeypatch):
    """The signed request uses the active endpoint, exact body, and timeout."""
    client = _invoke_client([_deployment()])
    session = Mock()
    session.post.return_value = _successful_response(
        {"id": "response-id", "sessionKey": "session-id", "output": []}
    )
    monkeypatch.setattr(agents, "agent_clients", lambda _settings: _clients(client))
    monkeypatch.setattr(agents, "load_auth", Mock(return_value=({}, {"signer": "s"})))
    monkeypatch.setattr(agents.requests, "Session", Mock(return_value=session))

    result = agents.invoke_agent(
        SimpleNamespace(),
        "hello",
        "Hi there",
        session_key="existing-session",
        timeout_seconds=42,
        confirm_invoke=True,
    )

    assert result["session_key"] == "session-id"
    session.post.assert_called_once_with(
        "https://gateway.aidp.example.oraclecloud.com/agentendpoint/id/chat",
        auth="s",
        json={
            "isStreamEnabled": False,
            "input": [
                {
                    "role": "User",
                    "content": [{"type": "INPUT_TEXT", "text": "Hi there"}],
                }
            ],
            "sessionKey": "existing-session",
        },
        timeout=42,
        allow_redirects=False,
    )
    session.close.assert_called_once_with()


def test_invoke_agent_bounds_response_text_and_reports_observable_keys(monkeypatch):
    """Only documented output text is concatenated and locally bounded."""
    client = _invoke_client([_deployment()])
    response = _successful_response(
        {
            "responseId": "response-id",
            "output": [
                {"content": [{"text": "hello"}, {"text": " world"}]},
                {"content": [{"type": "other"}, {"text": "!"}]},
            ],
        },
        {"X-Session-Key": "header-session"},
    )
    monkeypatch.setattr(agents, "agent_clients", lambda _settings: _clients(client))
    monkeypatch.setattr(agents, "load_auth", Mock(return_value=({}, {"signer": "s"})))
    monkeypatch.setattr(agents, "_post_agent_message", Mock(return_value=response))

    result = agents.invoke_agent(
        SimpleNamespace(), "hello", "Hi there", max_characters=7, confirm_invoke=True
    )

    assert result == {
        "agent_name": "hello",
        "http_status": 200,
        "response_id": "response-id",
        "session_key": "header-session",
        "session_key_note": None,
        "text": "hello w",
        "truncated": True,
        "response_keys": ["output", "responseId"],
    }


def test_invoke_agent_non_success_status_has_bounded_sanitized_excerpt(monkeypatch):
    """HTTP failures expose neither signer details nor an unbounded body."""
    client = _invoke_client([_deployment()])
    response = Mock(status_code=503, text="x" * 1200)
    post = Mock(return_value=response)
    monkeypatch.setattr(agents, "agent_clients", lambda _settings: _clients(client))
    monkeypatch.setattr(
        agents, "load_auth", Mock(return_value=({}, {"signer": "secret"}))
    )
    monkeypatch.setattr(agents, "_post_agent_message", post)

    with pytest.raises(AidpError) as error:
        agents.invoke_agent(SimpleNamespace(), "hello", "Hi there", confirm_invoke=True)

    message = str(error.value)
    assert "HTTP 503" in message
    assert "secret" not in message
    assert message.endswith("x" * 1000)


def test_invoke_agent_non_success_status_redacts_ocids(monkeypatch):
    """An HTTP failure replaces an OCI resource identifier before returning it."""
    client = _invoke_client([_deployment()])
    ocid = "ocid1.agent.oc1.eu-frankfurt-1.aaaaaaaexampleidentifier"
    response = Mock(status_code=503, text=f"Agent failure for {ocid}")
    monkeypatch.setattr(agents, "agent_clients", lambda _settings: _clients(client))
    monkeypatch.setattr(agents, "load_auth", Mock(return_value=({}, {"signer": "s"})))
    monkeypatch.setattr(agents, "_post_agent_message", Mock(return_value=response))

    with pytest.raises(AidpError) as error:
        agents.invoke_agent(SimpleNamespace(), "hello", "Hi there", confirm_invoke=True)

    assert "Agent failure for <ocid>" in str(error.value)
    assert ocid not in str(error.value)


def test_response_excerpt_without_ocids_is_unchanged():
    """A non-OCID error excerpt keeps its original text."""
    response = SimpleNamespace(text="The deployment cannot accept this message.")

    assert getattr(agents, "_response_excerpt")(response) == response.text


def test_invoke_agent_timeout_does_not_retry(monkeypatch):
    """Timeouts are actionable and the HTTP session receives one request only."""
    session = Mock()
    session.post.side_effect = agents.requests.Timeout()
    monkeypatch.setattr(agents.requests, "Session", Mock(return_value=session))

    with pytest.raises(AidpError, match="timed out; no retry"):
        getattr(agents, "_post_agent_message")(
            "https://example.oraclecloud.com/chat", "s", {}, 1
        )

    session.post.assert_called_once()
    session.close.assert_called_once_with()


def _agent_source(tmp_path, files):
    """Create a safe local agent source tree from relative byte payloads."""
    directory = tmp_path / "hello_agent"
    for relative_path, data in files.items():
        file_path = directory / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(data)
    return directory


def _upload_settings(root):
    """Return the minimum allowed-root settings used by upload tests."""
    return SimpleNamespace(allowed_roots=(root,))


def test_upload_agent_code_plans_create_update_unchanged_and_remote_only(
    tmp_path, monkeypatch
):
    """The default plan compares raw bytes and performs no workspace writes."""
    directory = _agent_source(
        tmp_path,
        {"hello.py": b"same", "nested/new.py": b"new", "changed.py": b"local"},
    )
    objects = Mock()
    remote = {
        "/Workspace/hello/hello.py": b"same",
        "/Workspace/hello/changed.py": b"remote",
        "/Workspace/hello/nested/new.py": None,
    }
    monkeypatch.setattr(
        agents, "workspace_clients", lambda _settings: _workspace_clients(objects)
    )
    monkeypatch.setattr(
        agents,
        "read_workspace_file",
        lambda _client, _instance, _workspace, path: remote[path],
    )
    remote_item = SimpleNamespace(path="/Workspace/hello/remote.py", type="FILE")
    monkeypatch.setattr(
        agents,
        "list_workspace_objects",
        lambda *_args: ([remote_item], False),
    )
    create_folder = Mock()
    upload = Mock()
    monkeypatch.setattr(agents, "create_workspace_folder", create_folder)
    monkeypatch.setattr(agents, "upload_workspace_file", upload)

    result = agents.upload_agent_code(
        _upload_settings(tmp_path), directory, "/Workspace/hello"
    )

    assert result == {
        "local_root": "hello_agent",
        "workspace_dir": "/Workspace/hello",
        "counts": {"create": 1, "update": 1, "unchanged": 1},
        "files": [
            {
                "relative_path": "changed.py",
                "action": "update",
                "size": 5,
                "sha256": result["files"][0]["sha256"],
            },
            {
                "relative_path": "hello.py",
                "action": "unchanged",
                "size": 4,
                "sha256": result["files"][1]["sha256"],
            },
            {
                "relative_path": "nested/new.py",
                "action": "create",
                "size": 3,
                "sha256": result["files"][2]["sha256"],
            },
        ],
        "remote_only": ["remote.py"],
        "apply": False,
    }
    assert all(len(entry["sha256"]) == 12 for entry in result["files"])
    create_folder.assert_not_called()
    upload.assert_not_called()


def test_upload_agent_code_rejects_updates_before_any_write(tmp_path, monkeypatch):
    """An update without explicit overwrite cannot create folders or files."""
    directory = _agent_source(tmp_path, {"hello.py": b"local"})
    objects = Mock()
    monkeypatch.setattr(
        agents, "workspace_clients", lambda _settings: _workspace_clients(objects)
    )
    monkeypatch.setattr(agents, "read_workspace_file", Mock(return_value=b"remote"))
    monkeypatch.setattr(agents, "list_workspace_objects", lambda *_args: ([], False))
    create_folder = Mock()
    upload = Mock()
    monkeypatch.setattr(agents, "create_workspace_folder", create_folder)
    monkeypatch.setattr(agents, "upload_workspace_file", upload)

    with pytest.raises(AidpError, match="overwrite=true"):
        agents.upload_agent_code(
            _upload_settings(tmp_path), directory, "/Workspace/hello", apply=True
        )

    create_folder.assert_not_called()
    upload.assert_not_called()


def test_upload_agent_code_creates_parent_folders_and_verifies_files(
    tmp_path, monkeypatch
):
    """Apply creates folders in order, skips unchanged files, and reads uploads back."""
    directory = _agent_source(
        tmp_path, {"unchanged.py": b"same", "nested/new.py": b"new"}
    )
    objects = Mock()
    read_counts = {"/Workspace/hello/nested/new.py": 0}

    def read(_client, _instance, _workspace, path):
        if path.endswith("unchanged.py"):
            return b"same"
        read_counts[path] += 1
        return None if read_counts[path] == 1 else b"new"

    create_folder = Mock()
    upload = Mock()
    monkeypatch.setattr(
        agents, "workspace_clients", lambda _settings: _workspace_clients(objects)
    )
    monkeypatch.setattr(agents, "read_workspace_file", read)
    monkeypatch.setattr(agents, "list_workspace_objects", lambda *_args: ([], False))
    monkeypatch.setattr(agents, "create_workspace_folder", create_folder)
    monkeypatch.setattr(agents, "upload_workspace_file", upload)

    result = agents.upload_agent_code(
        _upload_settings(tmp_path), directory, "/Workspace/hello", apply=True
    )

    assert [call.args[3] for call in create_folder.call_args_list] == [
        "/Workspace/hello",
        "/Workspace/hello/nested",
    ]
    upload.assert_called_once_with(
        objects,
        "instance",
        "workspace",
        "/Workspace/hello/nested/new.py",
        b"new",
        overwrite=False,
    )
    assert result["uploaded"] == result["verified"] == 1


def test_upload_agent_code_stops_on_readback_mismatch_and_lists_uploads(
    tmp_path, monkeypatch
):
    """A mismatched read-back fails safely after reporting the uploaded file."""
    directory = _agent_source(tmp_path, {"hello.py": b"local"})
    objects = Mock()
    monkeypatch.setattr(
        agents, "workspace_clients", lambda _settings: _workspace_clients(objects)
    )
    monkeypatch.setattr(
        agents, "read_workspace_file", Mock(side_effect=[None, b"wrong"])
    )
    monkeypatch.setattr(agents, "list_workspace_objects", lambda *_args: ([], False))
    monkeypatch.setattr(agents, "create_workspace_folder", Mock())
    monkeypatch.setattr(agents, "upload_workspace_file", Mock())

    with pytest.raises(AidpError, match="hello.py; uploaded files: hello.py"):
        agents.upload_agent_code(
            _upload_settings(tmp_path), directory, "/Workspace/hello", apply=True
        )


def test_upload_agent_code_reports_completed_files_after_mid_upload_failure(
    tmp_path, monkeypatch
):
    """A later upload failure leaves a rerunnable report of earlier uploads."""
    directory = _agent_source(tmp_path, {"first.py": b"first", "second.py": b"second"})
    objects = Mock()
    read = Mock(side_effect=[None, None, b"first"])
    upload = Mock(side_effect=[None, AidpError("upload rejected")])
    monkeypatch.setattr(
        agents, "workspace_clients", lambda _settings: _workspace_clients(objects)
    )
    monkeypatch.setattr(agents, "read_workspace_file", read)
    monkeypatch.setattr(agents, "list_workspace_objects", lambda *_args: ([], False))
    monkeypatch.setattr(agents, "create_workspace_folder", Mock())
    monkeypatch.setattr(agents, "upload_workspace_file", upload)

    with pytest.raises(AidpError, match="second.py; uploaded files: first.py"):
        agents.upload_agent_code(
            _upload_settings(tmp_path), directory, "/Workspace/hello", apply=True
        )


def test_upload_agent_code_reports_only_unchanged_after_a_successful_rerun(
    tmp_path, monkeypatch
):
    """A fully matching apply is a no-op with explicit zero upload counters."""
    directory = _agent_source(tmp_path, {"hello.py": b"same"})
    objects = Mock()
    monkeypatch.setattr(
        agents, "workspace_clients", lambda _settings: _workspace_clients(objects)
    )
    monkeypatch.setattr(agents, "read_workspace_file", Mock(return_value=b"same"))
    monkeypatch.setattr(agents, "list_workspace_objects", lambda *_args: ([], False))
    upload = Mock()
    monkeypatch.setattr(agents, "upload_workspace_file", upload)

    result = agents.upload_agent_code(
        _upload_settings(tmp_path), directory, "/Workspace/hello", apply=True
    )

    assert result["counts"] == {"create": 0, "update": 0, "unchanged": 1}
    assert result["uploaded"] == result["verified"] == 0
    upload.assert_not_called()
