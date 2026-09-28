"""
Author: L. Saetta
Date last modified: 2026-09-28
License: MIT
Description: Offline tests for read-only AI DP CODE-agent deployment planning.
"""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aidp_common.connection import AidpError
from aidp_mcp import agent_deploy
from aidp_mcp.tests.agent_fixtures import (
    collection_response as _response,
    make_agent as _agent,
)


@contextmanager
def _agent_clients(client):
    """Provide the minimal configured-workspace agent-client contract."""
    yield "instance", "workspace", client


@contextmanager
def _workspace_clients(clusters, objects):
    """Provide the minimal workspace clients for file and compute reads."""
    yield "instance", "workspace", clusters, Mock(), Mock(), objects


def _deployment(key="deployment-key", **values):
    """Build one representative SDK deployment summary."""
    defaults = {
        "key": key,
        "lifecycle_state": "ACTIVE",
        "deployment_type": "PROD",
        "deployment_version": None,
        "endpoint_url": "https://example.oraclecloud.com/agentendpoint/agent/chat",
        "time_created": "2026-09-28T08:00:00Z",
        "time_updated": None,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


def _plan_contexts(monkeypatch, *, agent=None, deployments=None, compute=None):
    """Configure ordinary read-only agent, file, and AI Compute planning mocks."""
    client = Mock()
    detail = agent or _agent(entry_file_path="/Workspace/hello/agent.py")
    client.list_agents.return_value = _response([detail])
    client.get_agent.return_value = SimpleNamespace(data=detail)
    client.list_agent_deployments.return_value = _response(deployments or [])
    clusters, objects = Mock(), Mock()
    compute = compute or SimpleNamespace(type="AI_COMPUTE", state="ACTIVE")
    monkeypatch.setattr(
        agent_deploy, "agent_clients", lambda _settings: _agent_clients(client)
    )
    monkeypatch.setattr(
        agent_deploy,
        "workspace_clients",
        lambda _settings: _workspace_clients(clusters, objects),
    )
    monkeypatch.setattr(agent_deploy, "read_workspace_file", Mock(return_value=b"x"))
    monkeypatch.setattr(
        agent_deploy,
        "find_cluster_details",
        Mock(return_value=SimpleNamespace(data=compute)),
    )
    return client


def test_deploy_agent_plan_selects_first_deployment_without_writes(monkeypatch):
    """A verified CODE agent and ACTIVE AI Compute produce a deploy plan only."""
    client = _plan_contexts(monkeypatch)

    result = agent_deploy.deploy_agent(SimpleNamespace(), "hello", "aicomp02")

    assert result == {
        "action": "deploy",
        "agent_name": "hello",
        "compute_name": "aicomp02",
        "current_deployment": None,
        "notes": agent_deploy.DEPLOYMENT_NOTES,
    }
    client.deploy_agent.assert_not_called()
    client.redeploy_agent_by_key.assert_not_called()


def test_deploy_agent_plan_selects_active_prod_redeployment(monkeypatch):
    """One active PROD deployment is a redeploy plan with selected fields only."""
    active = _deployment()
    deleted = _deployment("deleted", lifecycle_state="DELETED")
    playground = _deployment("test", deployment_type="TEST")
    client = _plan_contexts(monkeypatch, deployments=[active, deleted, playground])

    result = agent_deploy.deploy_agent(SimpleNamespace(), "hello", "aicomp02")

    assert result["action"] == "redeploy"
    assert result["current_deployment"] == {
        "key": "deployment-key",
        "lifecycle_state": "ACTIVE",
        "deployment_type": "PROD",
        "time_created": "2026-09-28T08:00:00Z",
        "endpoint_url": active.endpoint_url,
    }
    client.deploy_agent.assert_not_called()
    client.redeploy_agent_by_key.assert_not_called()


@pytest.mark.parametrize(
    ("agent", "source", "message"),
    [
        (_agent(type="CANVAS"), b"source", "type CODE"),
        (_agent(entry_file_path="/Workspace/hello/agent.py"), None, "does not exist"),
        (_agent(entry_file_path=None), b"source", "missing its entry_file_path"),
    ],
)
def test_deploy_agent_plan_rejects_non_deployable_agent_or_source(
    monkeypatch, agent, source, message
):
    """Wrong agent types and absent configured files never reach compute lookup."""
    _plan_contexts(monkeypatch, agent=agent)
    monkeypatch.setattr(agent_deploy, "read_workspace_file", Mock(return_value=source))
    compute_lookup = Mock()
    monkeypatch.setattr(agent_deploy, "find_cluster_details", compute_lookup)

    with pytest.raises(AidpError, match=message):
        agent_deploy.deploy_agent(SimpleNamespace(), "hello", "aicomp02")

    compute_lookup.assert_not_called()


def test_deploy_agent_plan_rejects_missing_dependencies_file(monkeypatch):
    """An optional configured dependency file is required when it is set."""
    agent = _agent(
        entry_file_path="/Workspace/hello/agent.py",
        dependencies_file_path="/Workspace/hello/requirements.txt",
    )
    _plan_contexts(monkeypatch, agent=agent)
    monkeypatch.setattr(
        agent_deploy, "read_workspace_file", Mock(side_effect=[b"source", None])
    )

    with pytest.raises(AidpError, match="dependencies_file_path does not exist"):
        agent_deploy.deploy_agent(SimpleNamespace(), "hello", "aicomp02")


@pytest.mark.parametrize(
    ("compute", "message"),
    [
        (SimpleNamespace(type="USER", state="ACTIVE"), "ACTIVE AI_COMPUTE"),
        (SimpleNamespace(type="AI_COMPUTE", state="STOPPED"), "set_cluster_state"),
    ],
)
def test_deploy_agent_plan_rejects_non_active_or_wrong_compute(
    monkeypatch, compute, message
):
    """Compute failures explain status recovery and the Lakehouse dependency."""
    _plan_contexts(monkeypatch, compute=compute)

    with pytest.raises(AidpError, match=message) as error:
        agent_deploy.deploy_agent(SimpleNamespace(), "hello", "aicomp02")

    assert "get_cluster_status" in str(error.value)
    assert "AI Lakehouse" in str(error.value)


def test_deploy_agent_plan_rejects_missing_compute_with_recovery(monkeypatch):
    """Missing computes receive the same safe, actionable recovery guidance."""
    _plan_contexts(monkeypatch)
    monkeypatch.setattr(
        agent_deploy,
        "find_cluster_details",
        Mock(side_effect=AidpError("Expected exactly one cluster named 'missing'.")),
    )

    with pytest.raises(AidpError, match="ACTIVE AI_COMPUTE") as error:
        agent_deploy.deploy_agent(SimpleNamespace(), "hello", "missing")

    assert "get_cluster_status" in str(error.value)
    assert "AI Lakehouse" in str(error.value)


@pytest.mark.parametrize(
    "deployments",
    [
        [_deployment("first"), _deployment("second")],
        [_deployment(lifecycle_state="CREATING")],
        [_deployment(lifecycle_state="FAILED")],
    ],
)
def test_deploy_agent_plan_rejects_ambiguous_or_unhealthy_deployments(
    monkeypatch, deployments
):
    """The plan reports deployment states instead of guessing a write action."""
    _plan_contexts(monkeypatch, deployments=deployments)

    with pytest.raises(AidpError, match="deployment states"):
        agent_deploy.deploy_agent(SimpleNamespace(), "hello", "aicomp02")


def test_deploy_agent_apply_is_rejected_before_remote_client_creation(monkeypatch):
    """Step 2 cannot submit a deployment even when a caller sets apply=true."""
    agent_context = Mock()
    workspace_context = Mock()
    monkeypatch.setattr(agent_deploy, "agent_clients", agent_context)
    monkeypatch.setattr(agent_deploy, "workspace_clients", workspace_context)

    with pytest.raises(AidpError, match="not available"):
        agent_deploy.deploy_agent(SimpleNamespace(), "hello", "aicomp02", apply=True)

    agent_context.assert_not_called()
    workspace_context.assert_not_called()


@pytest.mark.parametrize(
    ("apply", "wait", "timeout_seconds"),
    [("false", True, 300), (False, "true", 300), (False, True, 29)],
)
def test_deploy_agent_validates_signature_before_remote_client_creation(
    monkeypatch, apply, wait, timeout_seconds
):
    """Invalid future apply settings cannot trigger planning reads."""
    agent_context = Mock()
    monkeypatch.setattr(agent_deploy, "agent_clients", agent_context)

    with pytest.raises(AidpError):
        agent_deploy.deploy_agent(
            SimpleNamespace(),
            "hello",
            "aicomp02",
            apply=apply,
            wait=wait,
            timeout_seconds=timeout_seconds,
        )

    agent_context.assert_not_called()
