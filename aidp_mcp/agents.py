"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Read-only AI DP agent discovery and deployment observation.
"""

from aidp_common.connection import AidpError
from aidp_mcp.lookups import next_page, resource_key
from aidp_mcp.targets import agent_clients
from aidp_mcp.validation import _validate_result_limit, validate_resource_name

MAX_AGENT_RESULTS = 1000
MAX_AGENT_DEPLOYMENTS = 1000


def list_agents(settings, name_contains=None, max_results=50):
    """List bounded, sanitized agent metadata in the configured workspace.

    This is read-only. ``name_contains`` is an optional case-insensitive
    display-name substring; results remain bounded by ``max_results``.

    Args:
        settings: Validated MCP connection settings.
        name_contains: Optional case-insensitive display-name substring.
        max_results: Maximum agents returned, from 1 through 1,000.

    Returns:
        dict: Sanitized agents and whether additional matching results exist.

    Raises:
        AidpError: A filter or result bound is invalid.
    """
    if name_contains is not None and (
        not isinstance(name_contains, str) or not name_contains.strip()
    ):
        raise AidpError("name_contains must be a nonempty string when provided.")
    _validate_result_limit(max_results, MAX_AGENT_RESULTS)
    agents = []
    page = None
    truncated = False
    with agent_clients(settings) as clients:
        instance_id, workspace_key, client = clients
        while len(agents) < max_results:
            response = client.list_agents(
                instance_id,
                workspace_key,
                display_name_contains=name_contains,
                limit=max_results - len(agents),
                page=page,
            )
            items = _collection_items(response)
            for index, agent in enumerate(items):
                agents.append(_agent_response(agent))
                if len(agents) == max_results:
                    truncated = index < len(items) - 1
                    break
            page = next_page(response)
            truncated = truncated or bool(page)
            if not page:
                break
    return {"agents": agents, "truncated": truncated}


def get_agent(settings, agent_name):
    """Read one exact-name agent and its bounded deployment metadata.

    This is read-only. Agent display-name matching is exact and
    case-sensitive; deployment metadata is bounded to 1,000 entries.

    Args:
        settings: Validated MCP connection settings.
        agent_name: Exact, case-sensitive agent display name.

    Returns:
        dict: Sanitized agent metadata and deployments.

    Raises:
        AidpError: The agent name is invalid, absent, or ambiguous.
    """
    agent_name = validate_resource_name(agent_name, "Agent")
    with agent_clients(settings) as clients:
        instance_id, workspace_key, client = clients
        agent = _find_agent(client, instance_id, workspace_key, agent_name)
        agent_key = resource_key(agent, "Agent")
        detail = client.get_agent(instance_id, workspace_key, agent_key).data
        deployments, truncated = _list_deployments(
            client, instance_id, workspace_key, agent_key
        )
    result = _agent_response(detail)
    result["deployments"] = deployments
    result["deployments_truncated"] = truncated
    return result


def _find_agent(client, instance_id, workspace_key, agent_name):
    """Resolve exactly one agent display name without trusting server filtering."""
    matches = []
    page = None
    while len(matches) < 2:
        response = client.list_agents(
            instance_id,
            workspace_key,
            display_name=agent_name,
            limit=MAX_AGENT_RESULTS,
            page=page,
        )
        for agent in _collection_items(response):
            if getattr(agent, "display_name", None) == agent_name:
                matches.append(agent)
                if len(matches) == 2:
                    break
        page = next_page(response)
        if not page:
            break
    if len(matches) != 1:
        raise AidpError(
            f"Agent name {agent_name!r} has {len(matches)} visible exact matches."
        )
    return matches[0]


def _list_deployments(client, instance_id, workspace_key, agent_key):
    """Return a bounded deployment list for one already-resolved agent."""
    deployments = []
    page = None
    truncated = False
    while len(deployments) < MAX_AGENT_DEPLOYMENTS:
        response = client.list_agent_deployments(
            instance_id,
            workspace_key,
            agent_key,
            limit=MAX_AGENT_DEPLOYMENTS - len(deployments),
            page=page,
        )
        items = _collection_items(response)
        for index, deployment in enumerate(items):
            deployments.append(_deployment_response(deployment))
            if len(deployments) == MAX_AGENT_DEPLOYMENTS:
                truncated = index < len(items) - 1
                break
        page = next_page(response)
        truncated = truncated or bool(page)
        if not page:
            break
    return deployments, truncated


def _collection_items(response):
    """Return SDK collection items while tolerating an empty response model."""
    return getattr(getattr(response, "data", None), "items", None) or []


def _agent_response(agent):
    """Return selected agent fields without serializing SDK objects."""
    compute_key = getattr(agent, "compute_key", None)
    deployment_compute_key = getattr(agent, "deployment_compute_key", None)
    return {
        "name": getattr(agent, "display_name", None),
        "key": resource_key(agent, "Agent"),
        "type": getattr(agent, "type", None),
        "lifecycle_state": getattr(agent, "lifecycle_state", None),
        "lifecycle_details": getattr(agent, "lifecycle_details", None),
        "deployment_mode": getattr(agent, "deployment_mode", None),
        "uri_state": getattr(agent, "uri_state", None),
        "entry_file_path": getattr(agent, "entry_file_path", None),
        "dependencies_file_path": getattr(agent, "dependencies_file_path", None),
        "path_info": getattr(agent, "path_info", None),
        "compute_attached": bool(compute_key or deployment_compute_key),
    }


def _deployment_response(deployment):
    """Return selected deployment fields without serializing SDK objects."""
    return {
        "key": resource_key(deployment, "Agent deployment"),
        "lifecycle_state": getattr(deployment, "lifecycle_state", None),
        "deployment_type": getattr(deployment, "deployment_type", None),
        "deployment_version": getattr(deployment, "deployment_version", None),
        "endpoint_url": getattr(deployment, "endpoint_url", None),
        "time_created": getattr(deployment, "time_created", None),
        "time_updated": getattr(deployment, "time_updated", None),
    }
