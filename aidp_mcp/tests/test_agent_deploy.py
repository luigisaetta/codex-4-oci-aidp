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


@contextmanager
def _async_operations_clients(client):
    """Provide the minimal instance-scoped async-operations client contract."""
    yield "instance", client


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
    compute = compute or SimpleNamespace(
        key="compute-key", type="AI_COMPUTE", state="ACTIVE"
    )
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


def test_deploy_agent_plan_ignores_failed_and_creating_test_deployments(
    monkeypatch,
):
    """Playground deployment failures cannot block a first PROD deploy plan."""
    failed_test = _deployment(
        "failed-test", lifecycle_state="FAILED", deployment_type="TEST"
    )
    creating_test = _deployment(
        "creating-test", lifecycle_state="CREATING", deployment_type="TEST"
    )
    client = _plan_contexts(monkeypatch, deployments=[failed_test, creating_test])

    result = agent_deploy.deploy_agent(SimpleNamespace(), "hello", "aicomp02")

    assert result["action"] == "deploy"
    assert result["current_deployment"] is None
    client.deploy_agent.assert_not_called()
    client.redeploy_agent_by_key.assert_not_called()


def test_deploy_agent_plan_ignores_failed_and_creating_test_for_redeploy(
    monkeypatch,
):
    """Playground deployment failures cannot block a safe PROD redeploy plan."""
    active = _deployment()
    failed_test = _deployment(
        "failed-test", lifecycle_state="FAILED", deployment_type="TEST"
    )
    creating_test = _deployment(
        "creating-test", lifecycle_state="CREATING", deployment_type="TEST"
    )
    _plan_contexts(monkeypatch, deployments=[failed_test, active, creating_test])

    result = agent_deploy.deploy_agent(SimpleNamespace(), "hello", "aicomp02")

    assert result["action"] == "redeploy"
    assert result["current_deployment"]["key"] == active.key


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


def test_deploy_agent_apply_submits_first_deployment_once_without_wait(monkeypatch):
    """First deployment uses the exact SDK details model and never retries."""
    client = _plan_contexts(monkeypatch)
    client.deploy_agent.return_value = SimpleNamespace(status=202)

    result = agent_deploy.deploy_agent(
        SimpleNamespace(), "hello", "aicomp02", apply=True, wait=False
    )

    assert result == {"action": "deploy", "http_status": 202, "state": "SUBMITTED"}
    call = client.deploy_agent.call_args
    assert call.args[:3] == ("instance", "workspace", "agent-key")
    details = call.args[3]
    assert type(details).__name__ == "DeployAgentDetails"
    assert details.agent_key == "agent-key"
    assert details.agent_compute_key == "compute-key"
    assert type(call.kwargs["retry_strategy"]).__name__ == "NoneRetryStrategy"
    client.redeploy_agent_by_key.assert_not_called()


def test_deploy_agent_waits_for_new_redeployment_creation_time(monkeypatch):
    """Redeployment completes only when ACTIVE and recreated after the plan."""
    previous = _deployment(time_created="2026-09-28T08:00:00Z")
    final = _deployment(time_created="2026-09-28T08:01:00Z")
    client = _plan_contexts(monkeypatch, deployments=[previous])
    client.redeploy_agent_by_key.return_value = SimpleNamespace(status=202)
    client.list_agent_deployments.side_effect = [
        _response([previous]),
        _response([final]),
    ]
    monkeypatch.setattr(agent_deploy.time, "monotonic", Mock(side_effect=[0, 0, 1]))

    result = agent_deploy.deploy_agent(
        SimpleNamespace(), "hello", "aicomp02", apply=True
    )

    call = client.redeploy_agent_by_key.call_args
    assert call.args[:3] == ("instance", "workspace", "agent-key")
    assert type(call.args[3]).__name__ == "UpdateAgentDeploymentDetails"
    assert call.args[3].agent_compute_key == "compute-key"
    assert type(call.kwargs["retry_strategy"]).__name__ == "NoneRetryStrategy"
    assert result["final_deployment"]["time_created"] == final.time_created
    assert result["previous_time_created"] == previous.time_created
    assert result["endpoint_stable"] is True
    assert result["async_operation"] is None
    assert "timed_out" not in result


def test_redeploy_wait_ignores_time_updated_and_deployment_version(monkeypatch):
    """Only a newer creation time can make a redeploy wait complete."""
    previous = _deployment()
    unchanged = SimpleNamespace(
        **{
            **previous.__dict__,
            "time_updated": "2026-09-28T08:01:00Z",
            "deployment_version": "new",
        }
    )
    client = _plan_contexts(monkeypatch, deployments=[previous])
    client.redeploy_agent_by_key.return_value = SimpleNamespace(status=202)
    client.list_agent_deployments.side_effect = [
        _response([previous]),
        _response([unchanged]),
        _response([unchanged]),
    ]
    monkeypatch.setattr(agent_deploy.time, "sleep", Mock())
    monkeypatch.setattr(
        agent_deploy.time, "monotonic", Mock(side_effect=[0, 0, 0, 30, 31])
    )

    result = agent_deploy.deploy_agent(
        SimpleNamespace(), "hello", "aicomp02", apply=True, timeout_seconds=30
    )

    assert result["timed_out"] is True


def test_deploy_agent_reports_failed_wait_with_matching_async_operation(monkeypatch):
    """A failed deployment stops once and returns the newest matching error."""
    failed = _deployment(lifecycle_state="FAILED", endpoint_url=None)
    client = _plan_contexts(monkeypatch)
    client.deploy_agent.return_value = SimpleNamespace(status=202)
    client.list_agent_deployments.side_effect = [_response([]), _response([failed])]
    operation_client = Mock()
    operation = SimpleNamespace(
        key="operation-key",
        action_type="DEPLOY_AGENT",
        resource_display_name="agent-key_PROD_1",
    )
    detail = SimpleNamespace(
        key="operation-key",
        status="FAILED",
        error_code="FAILED",
        error_message="ocid1.user.oc1..private failure",
    )
    operation_client.list_async_operations.return_value = _response([operation])
    operation_client.get_async_operation.return_value = SimpleNamespace(data=detail)
    monkeypatch.setattr(
        agent_deploy,
        "async_operations_clients",
        lambda _settings: _async_operations_clients(operation_client),
    )
    monkeypatch.setattr(agent_deploy.time, "monotonic", Mock(side_effect=[0, 0, 1]))

    result = agent_deploy.deploy_agent(
        SimpleNamespace(), "hello", "aicomp02", apply=True
    )

    assert result["state"] == "FAILED"
    assert result["async_operation"] == {
        "key": "operation-key",
        "status": "FAILED",
        "error_code": "FAILED",
        "error_message": "<ocid> failure",
    }
    assert (
        operation_client.list_async_operations.call_args.kwargs["resource_type"]
        == "AGENT"
    )


def test_deploy_wait_ignores_failed_and_creating_test_deployments(monkeypatch):
    """Only the new ACTIVE PROD deployment completes a wait with Playground noise."""
    failed_test = _deployment(
        "failed-test", lifecycle_state="FAILED", deployment_type="TEST"
    )
    creating_test = _deployment(
        "creating-test", lifecycle_state="CREATING", deployment_type="TEST"
    )
    active_prod = _deployment("prod", time_created="2026-09-28T08:01:00Z")
    client = _plan_contexts(monkeypatch)
    client.deploy_agent.return_value = SimpleNamespace(status=202)
    client.list_agent_deployments.side_effect = [
        _response([]),
        _response([failed_test, creating_test, active_prod]),
    ]
    monkeypatch.setattr(agent_deploy.time, "monotonic", Mock(side_effect=[0, 0, 1]))

    result = agent_deploy.deploy_agent(
        SimpleNamespace(), "hello", "aicomp02", apply=True
    )

    assert result["final_deployment"]["key"] == active_prod.key
    assert result["async_operation"] is None
    assert "timed_out" not in result


def test_redeploy_wait_ignores_an_older_failed_prod_deployment(monkeypatch):
    """A failed PROD deployment from before the request cannot end a redeploy wait."""
    previous = _deployment(time_created="2026-09-28T08:00:00Z")
    old_failed = _deployment(
        "old-failed", lifecycle_state="FAILED", time_created="2026-09-28T07:59:00Z"
    )
    client = _plan_contexts(monkeypatch, deployments=[previous])
    client.redeploy_agent_by_key.return_value = SimpleNamespace(status=202)
    client.list_agent_deployments.side_effect = [
        _response([previous]),
        _response([previous, old_failed]),
        _response([previous, old_failed]),
    ]
    sleep = Mock()
    monkeypatch.setattr(agent_deploy.time, "sleep", sleep)
    monkeypatch.setattr(
        agent_deploy.time, "monotonic", Mock(side_effect=[0, 0, 0, 30, 31])
    )

    result = agent_deploy.deploy_agent(
        SimpleNamespace(), "hello", "aicomp02", apply=True, timeout_seconds=30
    )

    assert result["timed_out"] is True
    assert result["final_deployment"]["key"] == previous.key
    assert result["async_operation"] is None
    sleep.assert_called_once_with(10)
    client.redeploy_agent_by_key.assert_called_once()


def test_redeploy_wait_reports_a_newer_failed_prod_deployment(monkeypatch):
    """A failed PROD deployment created after the plan ends a redeploy wait."""
    previous = _deployment(time_created="2026-09-28T08:00:00Z")
    new_failed = _deployment(
        "new-failed", lifecycle_state="FAILED", time_created="2026-09-28T08:01:00Z"
    )
    client = _plan_contexts(monkeypatch, deployments=[previous])
    client.redeploy_agent_by_key.return_value = SimpleNamespace(status=202)
    client.list_agent_deployments.side_effect = [
        _response([previous]),
        _response([previous, new_failed]),
    ]
    operation_client = Mock()
    operation_client.list_async_operations.return_value = _response([])
    monkeypatch.setattr(
        agent_deploy,
        "async_operations_clients",
        lambda _settings: _async_operations_clients(operation_client),
    )
    monkeypatch.setattr(agent_deploy.time, "monotonic", Mock(side_effect=[0, 0, 1]))

    result = agent_deploy.deploy_agent(
        SimpleNamespace(), "hello", "aicomp02", apply=True
    )

    assert result["state"] == "FAILED"
    assert result["final_deployment"]["key"] == new_failed.key
    assert result["async_operation"] is None
    client.redeploy_agent_by_key.assert_called_once()


def test_deploy_agent_reports_timeout_without_resubmitting(monkeypatch):
    """Timeout reports current state and leaves the single request in flight."""
    client = _plan_contexts(monkeypatch)
    client.deploy_agent.return_value = SimpleNamespace(status=202)
    client.list_agent_deployments.side_effect = [
        _response([]),
        _response([]),
        _response([]),
    ]
    sleep = Mock()
    monkeypatch.setattr(agent_deploy.time, "sleep", sleep)
    monkeypatch.setattr(
        agent_deploy.time, "monotonic", Mock(side_effect=[0, 0, 0, 30, 31])
    )

    result = agent_deploy.deploy_agent(
        SimpleNamespace(), "hello", "aicomp02", apply=True, timeout_seconds=30
    )

    assert result["timed_out"] is True
    assert result["final_deployment"] is None
    assert result["elapsed_s"] == 31
    sleep.assert_called_once_with(10)
    client.deploy_agent.assert_called_once()


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
