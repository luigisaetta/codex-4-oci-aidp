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
