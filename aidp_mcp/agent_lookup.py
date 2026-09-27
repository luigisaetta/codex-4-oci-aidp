"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Shared exact-name AI DP agent lookup and sanitized response helpers.
"""

from aidp_common.connection import AidpError
from aidp_mcp.lookups import SDK_PAGE_SIZE, next_page, resource_key

MAX_AGENT_DEPLOYMENTS = 1000
MAX_ERROR_EVENT_MESSAGE_CHARACTERS = 1000


def find_agent(client, instance_id, workspace_key, agent_name):
    """Resolve exactly one agent display name without trusting server filtering."""
    matches = find_agent_matches(client, instance_id, workspace_key, agent_name)
    if len(matches) != 1:
        raise AidpError(
            f"Agent name {agent_name!r} has {len(matches)} visible exact matches."
        )
    return matches[0]


def find_agent_matches(client, instance_id, workspace_key, agent_name):
    """Return at most two exact-name matches for an agent selection decision."""
    matches = []
    page = None
    while len(matches) < 2:
        response = client.list_agents(
            instance_id,
            workspace_key,
            display_name=agent_name,
            limit=min(SDK_PAGE_SIZE, 2 - len(matches)),
            page=page,
        )
        for agent in collection_items(response):
            if getattr(agent, "display_name", None) == agent_name:
                matches.append(agent)
                if len(matches) == 2:
                    break
        page = next_page(response)
        if not page:
            break
    return matches


def list_deployments(client, instance_id, workspace_key, agent_key):
    """Return a bounded deployment list for one already-resolved agent."""
    deployments = []
    page = None
    truncated = False
    while len(deployments) < MAX_AGENT_DEPLOYMENTS:
        response = client.list_agent_deployments(
            instance_id,
            workspace_key,
            agent_key,
            limit=min(SDK_PAGE_SIZE, MAX_AGENT_DEPLOYMENTS - len(deployments)),
            page=page,
        )
        items = collection_items(response)
        for index, deployment in enumerate(items):
            deployments.append(deployment_response(deployment))
            if len(deployments) == MAX_AGENT_DEPLOYMENTS:
                truncated = index < len(items) - 1
                break
        page = next_page(response)
        if page:
            truncated = True
            continue
        break
    return deployments, truncated


def get_agent_response(client, instance_id, workspace_key, agent_name):
    """Read one selected agent and return its established public response."""
    agent = find_agent(client, instance_id, workspace_key, agent_name)
    agent_key = resource_key(agent, "Agent")
    detail = client.get_agent(instance_id, workspace_key, agent_key).data
    deployments, truncated = list_deployments(
        client, instance_id, workspace_key, agent_key
    )
    result = agent_response(detail)
    result["deployments"] = deployments
    result["deployments_truncated"] = truncated
    return result


def collection_items(response):
    """Return SDK collection items while tolerating an empty response model."""
    return getattr(getattr(response, "data", None), "items", None) or []


def agent_response(agent):
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


def deployment_response(deployment):
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


def session_response(session):
    """Return selected session fields without serializing SDK objects."""
    return {
        "session_id": resource_key(session, "Agent session"),
        "display_name": getattr(session, "display_name", None),
        "lifecycle_state": getattr(session, "lifecycle_state", None),
        "time_created": getattr(session, "time_created", None),
        "time_started": getattr(session, "time_started", None),
        "time_ended": getattr(session, "time_ended", None),
        "duration": getattr(session, "duration", None),
        "tokens": getattr(session, "tokens", None),
    }


def message_text(message):
    """Extract text from one chat-history item without exposing other content."""
    content = getattr(message, "content", None)
    if isinstance(content, (list, tuple)):
        return "".join(
            value
            for value in (getattr(item, "text", None) for item in content)
            if isinstance(value, str)
        )
    text = getattr(content, "text", None)
    return text if isinstance(text, str) else ""


def message_response(message, text):
    """Return message metadata, text, and metadata keys without metadata values."""
    metadata = getattr(message, "metadata", None)
    return {
        "role": getattr(message, "role", None),
        "time_created": getattr(message, "time_created", None),
        "tool_name": getattr(message, "tool_name", None),
        "text": text,
        "metadata_keys": sorted(metadata) if isinstance(metadata, dict) else [],
    }


def time_sort_key(span):
    """Build a deterministic start-time ordering key for opaque SDK timestamps."""
    start_time = getattr(span, "start_time", None)
    if isinstance(start_time, (int, float)) and not isinstance(start_time, bool):
        return 0, start_time
    return 1, str(start_time)


def duration(item):
    """Return a numeric duration when two compatible timestamps are available."""
    start_time = getattr(item, "start_time", None)
    end_time = getattr(item, "end_time", None)
    try:
        duration_value = end_time - start_time
    except (TypeError, ValueError):
        return None
    return (
        duration_value.total_seconds()
        if hasattr(duration_value, "total_seconds")
        else duration_value
    )


def span_response(span):
    """Return a span without prompt-bearing attributes or non-error events."""
    return {
        "span_name": getattr(span, "span_name", None),
        "kind": getattr(span, "kind", None),
        "status": getattr(span, "status", None),
        "duration": duration(span),
        "error_events": error_events(getattr(span, "events", None) or []),
    }


def error_events(events):
    """Keep bounded exception/error event names and messages only."""
    return [
        {"name": getattr(event, "name", None), "message": error_message(event)}
        for event in events
        if is_error_event(event)
    ]


def is_error_event(event):
    """Recognize the standard exception/error event names without attributes."""
    name = getattr(event, "name", None)
    return isinstance(name, str) and (
        "error" in name.casefold() or "exception" in name.casefold()
    )


def error_message(event):
    """Extract a bounded standard error message without returning attributes."""
    attributes = getattr(event, "attributes", None)
    if not isinstance(attributes, dict):
        return None
    for key in ("exception.message", "error.message", "message"):
        value = attributes.get(key)
        if isinstance(value, str):
            return value[:MAX_ERROR_EVENT_MESSAGE_CHARACTERS]
    return None
