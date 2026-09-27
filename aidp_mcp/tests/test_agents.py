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
from aidp_mcp.tests.agent_fixtures import (
    collection_response as _response,
    make_agent as _agent,
)


@contextmanager
def _clients(client):
    """Provide a minimal agent-client context for one offline test."""
    yield "instance", "workspace", client


def _resolved_client(agent=None):
    """Return a client whose exact-name lookup resolves one agent."""
    client = Mock()
    client.list_agents.return_value = _response([agent or _agent()])
    return client


def test_list_agents_paginates_bounds_and_sanitizes_results(monkeypatch):
    """Agent listing returns only selected fields up to the caller bound."""
    client = Mock()
    client.list_agents.side_effect = [
        _response([_agent("first"), _agent("second")], "later"),
        _response([_agent("third"), _agent("fourth")]),
    ]
    monkeypatch.setattr(agents, "agent_clients", lambda _settings: _clients(client))

    result = agents.list_agents(SimpleNamespace(), "agent", 3)

    assert [item["name"] for item in result["agents"]] == ["first", "second", "third"]
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
    """Exact reads retrieve details and safely bounded deployment metadata."""
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
