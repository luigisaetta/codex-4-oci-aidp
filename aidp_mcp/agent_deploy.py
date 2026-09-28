"""
Author: L. Saetta
Date last modified: 2026-09-28
License: MIT
Description: Read-only planning for safe AI DP CODE-agent deployment.
"""

from aidp_common.connection import AidpError
from aidp_mcp.agent_lookup import find_agent, list_deployments
from aidp_mcp.lookups import find_cluster_details, resource_key
from aidp_mcp.targets import agent_clients, workspace_clients
from aidp_mcp.validation import validate_resource_name
from aidp_mcp.workspace_files import read_workspace_file

DEPLOYMENT_NOTES = [
    "A redeploy recreates the deployment.",
    "The endpoint URL is expected to stay stable.",
    "Requests may fail while the redeploy is in progress.",
    "A redeploy is required after every code upload.",
]


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

    The plan verifies an ACTIVE AI Compute and every configured source file;
    it never changes an agent, compute, deployment, or endpoint. Deployment
    submission for ``apply=true`` is introduced in execution-plan step 3.

    Args:
        settings: Validated MCP connection settings.
        agent_name: Exact, case-sensitive CODE-agent display name.
        compute_name: Exact, case-sensitive AI Compute display name.
        apply: Reserved deployment-submission flag, currently rejected locally.
        wait: Reserved wait flag for the later deployment-submission step.
        timeout_seconds: Reserved later wait timeout, from 30 through 900.

    Returns:
        dict: Read-only deploy or redeploy plan with deployment notes.

    Raises:
        AidpError: A selector, target, source file, or deployment state is unsafe.
    """
    _validate_plan_arguments(apply, wait, timeout_seconds)
    agent_name = validate_resource_name(agent_name, "Agent")
    compute_name = validate_resource_name(compute_name, "Compute")
    if apply:
        raise AidpError(
            "apply=true is not available until the deployment submission step is "
            "implemented. Request the read-only plan with apply=false."
        )

    agent, deployments = _resolve_deployable_agent(settings, agent_name)
    _require_agent_source_files(settings, agent)
    _require_active_ai_compute(settings, compute_name)
    action, current = _deployment_action(deployments)
    return {
        "action": action,
        "agent_name": agent_name,
        "compute_name": compute_name,
        "current_deployment": current,
        "notes": DEPLOYMENT_NOTES,
    }


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
