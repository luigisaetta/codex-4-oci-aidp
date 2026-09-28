"""
Author: L. Saetta
Date last modified: 2026-09-28
License: MIT
Description: Guarded AI DP CODE-agent deployment planning, submission, and waiting.
"""

from datetime import datetime
import re
import time

import oci
from aidp_python_client.aidataplatform_dp import models

from aidp_common.connection import AidpError
from aidp_mcp.agent_lookup import (
    collection_items,
    find_agent,
    json_value,
    list_deployments,
)
from aidp_mcp.lookups import (
    SDK_PAGE_SIZE,
    find_cluster_details,
    next_page,
    resource_key,
)
from aidp_mcp.safety import should_apply
from aidp_mcp.targets import agent_clients, async_operations_clients, workspace_clients
from aidp_mcp.validation import validate_resource_name
from aidp_mcp.workspace_files import read_workspace_file

DEPLOYMENT_NOTES = [
    "A redeploy recreates the deployment.",
    "The endpoint URL is expected to stay stable.",
    "Requests may fail while the redeploy is in progress.",
    "A redeploy is required after every code upload.",
]
OCID_PATTERN = re.compile(
    r"ocid1\.[a-z0-9_-]+\.[a-z0-9_-]*\.[a-z0-9_-]*\.[A-Za-z0-9._-]+"
)
MAX_ASYNC_OPERATION_FIELD_CHARACTERS = 1000
MAX_DEPLOY_OPERATION_PAGES = 10


def deploy_agent(
    settings,
    agent_name,
    compute_name,
    *,
    apply=False,
    wait=True,
    timeout_seconds=300,
):
    """AI DP agent deploy: plan deployment of a CODE agent by default.

    The plan verifies an ACTIVE AI Compute and every configured source file.
    With ``apply=true``, it deploys or redeploys using already-running compute;
    the endpoint can be briefly unavailable during a redeploy. Nothing is
    deleted.

    Args:
        settings: Validated MCP connection settings.
        agent_name: Exact, case-sensitive CODE-agent display name.
        compute_name: Exact, case-sensitive AI Compute display name.
        apply: Submit the planned deployment or redeployment when true.
        wait: Wait for the verified ACTIVE deployment state after submission.
        timeout_seconds: Maximum wait time, from 30 through 900 seconds.

    Returns:
        dict: Read-only plan, submission receipt, or bounded final outcome.

    Raises:
        AidpError: A selector, target, source file, state, or request is unsafe.
    """
    _validate_plan_arguments(apply, wait, timeout_seconds)
    agent_name = validate_resource_name(agent_name, "Agent")
    compute_name = validate_resource_name(compute_name, "Compute")
    agent, deployments = _resolve_deployable_agent(settings, agent_name)
    _require_agent_source_files(settings, agent)
    compute = _require_active_ai_compute(settings, compute_name)
    action, current = _deployment_action(deployments)
    plan = {
        "action": action,
        "agent_name": agent_name,
        "compute_name": compute_name,
        "current_deployment": current,
        "notes": DEPLOYMENT_NOTES,
    }
    if not should_apply(apply, action):
        return plan
    return _submit_deployment(
        settings,
        action=action,
        agent=agent,
        compute=compute,
        previous=current,
        wait=wait,
        timeout_seconds=timeout_seconds,
    )


def _validate_plan_arguments(apply, wait, timeout_seconds):
    """Validate the complete public signature before remote target lookup."""
    if apply is not True and apply is not False:
        raise AidpError("apply must be a boolean.")
    if wait is not True and wait is not False:
        raise AidpError("wait must be a boolean.")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or not 30 <= timeout_seconds <= 900
    ):
        raise AidpError("timeout_seconds must be an integer from 30 through 900.")


def _resolve_deployable_agent(settings, agent_name):
    """Resolve a CODE agent and obtain all bounded deployment summaries."""
    with agent_clients(settings) as clients:
        instance_id, workspace_key, client = clients
        summary = find_agent(client, instance_id, workspace_key, agent_name)
        agent_key = resource_key(summary, "Agent")
        agent = client.get_agent(instance_id, workspace_key, agent_key).data
        if getattr(agent, "type", None) != "CODE":
            raise AidpError("Selected agent must have type CODE before deployment.")
        deployments, truncated = list_deployments(
            client, instance_id, workspace_key, agent_key
        )
    if truncated:
        raise AidpError(
            "Agent deployment list exceeded the safe planning bound; inspect "
            "deployments before trying again."
        )
    return agent, deployments


def _require_agent_source_files(settings, agent):
    """Confirm the CODE agent's entry and optional dependency files exist."""
    source_files = [
        ("entry_file_path", getattr(agent, "entry_file_path", None)),
        ("dependencies_file_path", getattr(agent, "dependencies_file_path", None)),
    ]
    for label, path in source_files:
        if path is not None and (not isinstance(path, str) or not path):
            raise AidpError(f"Agent {label} must be a nonempty workspace path.")
    entry_path = source_files[0][1]
    if entry_path is None:
        raise AidpError("CODE agent is missing its entry_file_path.")
    with workspace_clients(settings) as clients:
        instance_id, workspace_key, _, _, _, objects = clients
        for label, path in source_files:
            if (
                path is not None
                and read_workspace_file(objects, instance_id, workspace_key, path)
                is None
            ):
                raise AidpError(
                    f"Agent {label} does not exist in the workspace: {path}."
                )


def _require_active_ai_compute(settings, compute_name):
    """Resolve one exact ACTIVE AI Compute with recovery-oriented diagnostics."""
    with workspace_clients(settings) as clients:
        instance_id, workspace_key, clusters, _, _, _ = clients
        try:
            compute = find_cluster_details(
                clusters, instance_id, workspace_key, compute_name
            ).data
        except AidpError as error:
            raise AidpError(_compute_recovery_message("not found")) from error
    cluster_type = getattr(compute, "type", None)
    state = getattr(compute, "state", None)
    if cluster_type != "AI_COMPUTE" or state != "ACTIVE":
        raise AidpError(
            _compute_recovery_message(f"type={cluster_type!r}, state={state!r}")
        )
    return compute


def _compute_recovery_message(current_state):
    """Return one safe, actionable recovery message for compute validation."""
    return (
        "Selected compute must be an ACTIVE AI_COMPUTE "
        f"({current_state}). Use get_cluster_status to inspect it; if it is "
        "stopped, request approval for set_cluster_state. AI Compute depends on "
        "the AI Lakehouse being available."
    )


def _deployment_action(deployments):
    """Select one PROD action while leaving unrelated TEST deployments alone."""
    retained = [
        deployment
        for deployment in deployments
        if deployment.get("lifecycle_state") != "DELETED"
    ]
    if any(
        deployment.get("lifecycle_state") in {"CREATING", "FAILED"}
        for deployment in retained
    ):
        raise _ambiguous_deployment_error(retained)
    prod_deployments = [
        deployment
        for deployment in retained
        if deployment.get("deployment_type") == "PROD"
    ]
    if not prod_deployments:
        return "deploy", None
    if (
        len(prod_deployments) == 1
        and prod_deployments[0].get("lifecycle_state") == "ACTIVE"
    ):
        return "redeploy", _deployment_summary(prod_deployments[0])
    raise _ambiguous_deployment_error(retained)


def _ambiguous_deployment_error(deployments):
    """Build a state-bearing error for an unsafe deployment selection."""
    states = [
        f"{item.get('deployment_type')}:{item.get('lifecycle_state')}"
        for item in deployments
    ]
    return AidpError(
        "Cannot safely select a deployment action; found deployment states: "
        f"{', '.join(states)}."
    )


def _deployment_summary(deployment):
    """Return only the current deployment fields needed by the public plan."""
    return {
        field: deployment.get(field)
        for field in (
            "key",
            "lifecycle_state",
            "deployment_type",
            "time_created",
            "endpoint_url",
        )
    }


def _submit_deployment(
    settings, *, action, agent, compute, previous, wait, timeout_seconds
):
    """Submit one planned deployment without retries, then optionally observe it."""
    agent_key = resource_key(agent, "Agent")
    compute_key = resource_key(compute, "AI Compute")
    started = time.monotonic()
    with agent_clients(settings) as clients:
        instance_id, workspace_key, client = clients
        response = _submit_deployment_request(
            client,
            instance_id=instance_id,
            workspace_key=workspace_key,
            agent_key=agent_key,
            compute_key=compute_key,
            action=action,
        )
        if not wait:
            return {
                "action": action,
                "http_status": response.status,
                "state": "SUBMITTED",
            }
        return _wait_for_deployment(
            client,
            instance_id=instance_id,
            workspace_key=workspace_key,
            agent_key=agent_key,
            action=action,
            previous=previous,
            http_status=response.status,
            started=started,
            timeout_seconds=timeout_seconds,
            settings=settings,
        )


def _submit_deployment_request(
    client, *, instance_id, workspace_key, agent_key, compute_key, action
):
    """Call the exact generated SDK deployment method once without retries."""
    options = {"retry_strategy": oci.retry.NoneRetryStrategy()}
    try:
        if action == "deploy":
            return client.deploy_agent(
                instance_id,
                workspace_key,
                agent_key,
                models.DeployAgentDetails(
                    agent_key=agent_key, agent_compute_key=compute_key
                ),
                **options,
            )
        return client.redeploy_agent_by_key(
            instance_id,
            workspace_key,
            agent_key,
            models.UpdateAgentDeploymentDetails(
                agent_key=agent_key, agent_compute_key=compute_key
            ),
            **options,
        )
    except oci.exceptions.RequestException as error:
        raise AidpError(
            "Deployment request outcome is unknown; inspect the agent and async "
            "operations before retrying."
        ) from error


def _wait_for_deployment(
    client,
    *,
    instance_id,
    workspace_key,
    agent_key,
    action,
    previous,
    http_status,
    started,
    timeout_seconds,
    settings,
):
    """Poll deployment state without resubmitting or cancelling on timeout."""
    deadline = started + timeout_seconds
    while time.monotonic() < deadline:
        deployments, truncated = list_deployments(
            client, instance_id, workspace_key, agent_key
        )
        if truncated:
            raise AidpError("Deployment list exceeded the safe waiting bound.")
        outcome = _deployment_wait_outcome(action, deployments, previous)
        if outcome["state"] == "FAILED":
            outcome["async_operation"] = _latest_deploy_operation(settings, agent_key)
            return _deployment_result(
                action, http_status, previous, outcome, started, failed=True
            )
        if outcome["state"] == "ACTIVE":
            return _deployment_result(action, http_status, previous, outcome, started)
        time.sleep(min(10, max(0.1, deadline - time.monotonic())))

    deployments, truncated = list_deployments(
        client, instance_id, workspace_key, agent_key
    )
    if truncated:
        raise AidpError("Deployment list exceeded the safe waiting bound.")
    outcome = _deployment_wait_outcome(action, deployments, previous)
    if outcome["state"] == "FAILED":
        outcome["async_operation"] = _latest_deploy_operation(settings, agent_key)
        return _deployment_result(
            action, http_status, previous, outcome, started, failed=True
        )
    result = _deployment_result(action, http_status, previous, outcome, started)
    result["timed_out"] = True
    return result


def _deployment_wait_outcome(action, deployments, previous):
    """Classify current deployments using only verified lifecycle fields."""
    retained = [
        deployment
        for deployment in deployments
        if deployment.get("lifecycle_state") != "DELETED"
    ]
    prod_deployments = [
        deployment
        for deployment in retained
        if deployment.get("deployment_type") == "PROD"
    ]
    if any(deployment.get("lifecycle_state") == "FAILED" for deployment in retained):
        failed = next(
            deployment
            for deployment in retained
            if deployment.get("lifecycle_state") == "FAILED"
        )
        return {"state": "FAILED", "deployment": _deployment_summary(failed)}
    active = [
        deployment
        for deployment in prod_deployments
        if deployment.get("lifecycle_state") == "ACTIVE"
    ]
    if len(active) != 1:
        return {"state": "PENDING", "deployment": None}
    final = _deployment_summary(active[0])
    if action == "redeploy" and not _time_created_is_later(
        final["time_created"], previous["time_created"]
    ):
        return {"state": "PENDING", "deployment": final}
    return {"state": "ACTIVE", "deployment": final}


def _time_created_is_later(current, previous):
    """Compare deployment creation times without using update/version fields."""
    if current is None or previous is None:
        return False
    if isinstance(current, (int, float)) and isinstance(previous, (int, float)):
        return current > previous
    if isinstance(current, str) and isinstance(previous, str):
        try:
            return _parse_timestamp(current) > _parse_timestamp(previous)
        except ValueError:
            return False
    return False


def _parse_timestamp(value):
    """Parse the service's ISO timestamp representation for a safe comparison."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _deployment_result(
    action, http_status, previous, outcome, started, *, failed=False
):
    """Build a JSON-safe completed, failed, or pending deployment result."""
    final = outcome["deployment"]
    result = {
        "action": action,
        "http_status": http_status,
        "final_deployment": final,
        "previous_time_created": previous["time_created"] if previous else None,
        "endpoint_stable": (
            final is not None
            and previous is not None
            and final["endpoint_url"] == previous["endpoint_url"]
        ),
        "async_operation": outcome.get("async_operation"),
        "elapsed_s": round(time.monotonic() - started, 3),
    }
    if failed:
        result["state"] = "FAILED"
    return result


def _latest_deploy_operation(settings, agent_key):
    """Return the newest matching deployment operation with bounded errors."""
    with async_operations_clients(settings) as clients:
        instance_id, client = clients
        page = None
        for _ in range(MAX_DEPLOY_OPERATION_PAGES):
            response = client.list_async_operations(
                instance_id,
                resource_type="AGENT",
                limit=SDK_PAGE_SIZE,
                page=page,
                sort_by="timeStarted",
                sort_order="DESC",
            )
            for operation in collection_items(response):
                if getattr(
                    operation, "action_type", None
                ) == "DEPLOY_AGENT" and agent_key in (
                    getattr(operation, "resource_display_name", None) or ""
                ):
                    detail = client.get_async_operation(
                        instance_id, resource_key(operation, "Async operation")
                    ).data
                    return _async_operation_summary(detail)
            page = next_page(response)
            if not page:
                break
    return None


def _async_operation_summary(operation):
    """Return the selected sanitized fields needed for a failed deployment."""
    return {
        "key": _safe_operation_value(resource_key(operation, "Async operation")),
        "status": _safe_operation_value(getattr(operation, "status", None)),
        "error_code": _safe_operation_value(getattr(operation, "error_code", None)),
        "error_message": _safe_operation_value(
            getattr(operation, "error_message", None)
        ),
    }


def _safe_operation_value(value):
    """Convert operation fields to JSON-safe bounded values and mask OCIDs."""
    value = json_value(value)
    if not isinstance(value, str):
        return value
    return OCID_PATTERN.sub("<ocid>", value)[:MAX_ASYNC_OPERATION_FIELD_CHARACTERS]
