"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Offline tests for guarded AI DP deployed-agent invocation.
"""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aidp_common.connection import AidpError
from aidp_mcp import agent_invoke


@contextmanager
def _clients(client):
    """Provide a minimal agent-client context for one offline test."""
    yield "instance", "workspace", client


def _agent():
    """Build an exact-name agent fixture."""
    return SimpleNamespace(display_name="hello", key="agent-key")


def _response(items):
    """Build an OCI-like collection response."""
    return SimpleNamespace(data=SimpleNamespace(items=items), headers={})


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


def _invoke_client(deployments):
    """Return an exact-name agent client with chosen deployment summaries."""
    client = Mock()
    client.list_agents.return_value = _response([_agent()])
    client.list_agent_deployments.return_value = _response(deployments)
    return client


def _successful_response(body=None, headers=None):
    """Build a minimal successful HTTP response fixture."""
    response = Mock(status_code=200, headers=headers or {}, text="")
    response.json.return_value = body or {}
    return response


def test_invoke_agent_requires_confirmation_before_any_remote_call(monkeypatch):
    """An omitted confirmation cannot create clients, sessions, or HTTP calls."""
    client_context = Mock()
    http_call = Mock()
    monkeypatch.setattr(agent_invoke, "agent_clients", client_context)
    monkeypatch.setattr(agent_invoke, "_post_agent_message", http_call)

    with pytest.raises(AidpError, match="confirm_invoke=true"):
        agent_invoke.invoke_agent(SimpleNamespace(), "hello", "Hi there")

    client_context.assert_not_called()
    http_call.assert_not_called()


@pytest.mark.parametrize(
    "deployments, error", [([], "found 0"), ([_deployment(), _deployment()], "found 2")]
)
def test_invoke_agent_requires_exactly_one_active_deployment(
    monkeypatch, deployments, error
):
    """No deployment or multiple active deployments never send an HTTP request."""
    client = _invoke_client(deployments)
    http_call = Mock()
    monkeypatch.setattr(
        agent_invoke, "agent_clients", lambda _settings: _clients(client)
    )
    monkeypatch.setattr(agent_invoke, "_post_agent_message", http_call)

    with pytest.raises(AidpError, match=error):
        agent_invoke.invoke_agent(
            SimpleNamespace(), "hello", "Hi there", confirm_invoke=True
        )

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
    monkeypatch.setattr(
        agent_invoke, "agent_clients", lambda _settings: _clients(client)
    )
    monkeypatch.setattr(agent_invoke, "_post_agent_message", http_call)

    with pytest.raises(AidpError, match="invalid Oracle HTTPS"):
        agent_invoke.invoke_agent(
            SimpleNamespace(), "hello", "Hi there", confirm_invoke=True
        )

    http_call.assert_not_called()


def test_invoke_agent_sends_documented_request_without_redirects(monkeypatch):
    """The signed request uses the active endpoint, exact body, and timeout."""
    client = _invoke_client([_deployment()])
    session = Mock()
    session.post.return_value = _successful_response(
        {"id": "response-id", "sessionKey": "session-id", "output": []}
    )
    monkeypatch.setattr(
        agent_invoke, "agent_clients", lambda _settings: _clients(client)
    )
    monkeypatch.setattr(
        agent_invoke, "load_auth", Mock(return_value=({}, {"signer": "s"}))
    )
    monkeypatch.setattr(agent_invoke.requests, "Session", Mock(return_value=session))

    result = agent_invoke.invoke_agent(
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
    monkeypatch.setattr(
        agent_invoke, "agent_clients", lambda _settings: _clients(client)
    )
    monkeypatch.setattr(
        agent_invoke, "load_auth", Mock(return_value=({}, {"signer": "s"}))
    )
    monkeypatch.setattr(
        agent_invoke, "_post_agent_message", Mock(return_value=response)
    )

    result = agent_invoke.invoke_agent(
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
    monkeypatch.setattr(
        agent_invoke, "agent_clients", lambda _settings: _clients(client)
    )
    monkeypatch.setattr(
        agent_invoke, "load_auth", Mock(return_value=({}, {"signer": "secret"}))
    )
    monkeypatch.setattr(agent_invoke, "_post_agent_message", post)

    with pytest.raises(AidpError) as error:
        agent_invoke.invoke_agent(
            SimpleNamespace(), "hello", "Hi there", confirm_invoke=True
        )

    message = str(error.value)
    assert "HTTP 503" in message
    assert "secret" not in message
    assert message.endswith("x" * 1000)


def test_invoke_agent_non_success_status_redacts_ocids(monkeypatch):
    """An HTTP failure replaces an OCI resource identifier before returning it."""
    client = _invoke_client([_deployment()])
    ocid = "ocid1.agent.oc1.eu-frankfurt-1.aaaaaaaexampleidentifier"
    response = Mock(status_code=503, text=f"Agent failure for {ocid}")
    monkeypatch.setattr(
        agent_invoke, "agent_clients", lambda _settings: _clients(client)
    )
    monkeypatch.setattr(
        agent_invoke, "load_auth", Mock(return_value=({}, {"signer": "s"}))
    )
    monkeypatch.setattr(
        agent_invoke, "_post_agent_message", Mock(return_value=response)
    )

    with pytest.raises(AidpError) as error:
        agent_invoke.invoke_agent(
            SimpleNamespace(), "hello", "Hi there", confirm_invoke=True
        )

    assert "Agent failure for <ocid>" in str(error.value)
    assert ocid not in str(error.value)


def test_response_excerpt_without_ocids_is_unchanged():
    """A non-OCID error excerpt keeps its original text."""
    response = SimpleNamespace(text="The deployment cannot accept this message.")
    assert getattr(agent_invoke, "_response_excerpt")(response) == response.text


def test_invoke_agent_timeout_does_not_retry(monkeypatch):
    """Timeouts are actionable and the HTTP session receives one request only."""
    session = Mock()
    session.post.side_effect = agent_invoke.requests.Timeout()
    monkeypatch.setattr(agent_invoke.requests, "Session", Mock(return_value=session))

    with pytest.raises(AidpError, match="timed out; no retry"):
        getattr(agent_invoke, "_post_agent_message")(
            "https://example.oraclecloud.com/chat", "s", {}, 1
        )

    session.post.assert_called_once()
    session.close.assert_called_once_with()
