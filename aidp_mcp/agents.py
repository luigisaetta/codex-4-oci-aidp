"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Read-only AI DP agent discovery and deployment observation.
"""

from aidp_common.connection import AidpError, validate_resource_key
from aidp_mcp.lookups import next_page, resource_key
from aidp_mcp.targets import agent_clients
from aidp_mcp.validation import _validate_result_limit, validate_resource_name

MAX_AGENT_RESULTS = 1000
MAX_AGENT_DEPLOYMENTS = 1000
MAX_AGENT_SESSIONS = 1000
MAX_AGENT_MESSAGES = 1000
MAX_MESSAGE_CHARACTERS = 100000
MAX_TRACE_SPANS = 1000
MAX_ERROR_EVENT_MESSAGE_CHARACTERS = 1000


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


def list_agent_sessions(settings, agent_name, max_results=25):
    """List newest-first, bounded sessions for one exact-name agent.

    This read-only operation matches ``agent_name`` exactly and
    case-sensitively. It returns at most 1,000 sanitized session summaries.

    Args:
        settings: Validated MCP connection settings.
        agent_name: Exact, case-sensitive agent display name.
        max_results: Maximum sessions returned, from 1 through 1,000.

    Returns:
        dict: Sanitized session summaries and a truncation indicator.

    Raises:
        AidpError: The agent selection or result bound is invalid.
    """
    agent_name = validate_resource_name(agent_name, "Agent")
    _validate_result_limit(max_results, MAX_AGENT_SESSIONS)
    sessions = []
    page = None
    truncated = False
    with agent_clients(settings) as clients:
        instance_id, workspace_key, client = clients
        agent = _find_agent(client, instance_id, workspace_key, agent_name)
        agent_key = resource_key(agent, "Agent")
        while len(sessions) < max_results:
            response = client.list_agent_sessions(
                instance_id,
                workspace_key,
                agent_key,
                limit=max_results - len(sessions),
                page=page,
                sort_by="timeCreated",
                sort_order="DESC",
            )
            items = _collection_items(response)
            for index, session in enumerate(items):
                sessions.append(_session_response(session))
                if len(sessions) == max_results:
                    truncated = index < len(items) - 1
                    break
            page = next_page(response)
            truncated = truncated or bool(page)
            if not page:
                break
    return {"sessions": sessions, "truncated": truncated}


def get_agent_session_messages(settings, agent_name, session_id, max_characters=12000):
    """Read bounded messages for one exact-name agent session.

    This read-only operation matches ``agent_name`` exactly and
    case-sensitively. Messages can contain application data; request them
    only when authorized, as with job-run output. Text is bounded to 100,000
    characters and metadata values are never returned.

    Args:
        settings: Validated MCP connection settings.
        agent_name: Exact, case-sensitive agent display name.
        session_id: Existing single-segment agent session identifier.
        max_characters: Total returned message-text limit, from 1 through
            100,000.

    Returns:
        dict: Ordered, sanitized messages and a truncation indicator.

    Raises:
        AidpError: An input is invalid or the session cannot be read.
    """
    agent_name = validate_resource_name(agent_name, "Agent")
    validate_resource_key(session_id)
    _validate_result_limit(max_characters, MAX_MESSAGE_CHARACTERS)
    messages = []
    used_characters = 0
    page = None
    truncated = False
    with agent_clients(settings) as clients:
        instance_id, workspace_key, client = clients
        agent = _find_agent(client, instance_id, workspace_key, agent_name)
        agent_key = resource_key(agent, "Agent")
        while len(messages) < MAX_AGENT_MESSAGES and not truncated:
            response = client.list_agent_session_chat_histories(
                instance_id,
                workspace_key,
                agent_key,
                session_id,
                limit=MAX_AGENT_SESSIONS,
                page=page,
            )
            items = _collection_items(response)
            for index, message in enumerate(items):
                text = _message_text(message)
                remaining = max_characters - used_characters
                if len(text) > remaining:
                    text = text[:remaining]
                    truncated = True
                used_characters += len(text)
                messages.append(_message_response(message, text))
                if truncated or len(messages) == MAX_AGENT_MESSAGES:
                    truncated = truncated or index < len(items) - 1
                    break
            page = next_page(response)
            truncated = truncated or bool(page)
            if not page:
                break
    return {
        "session_id": session_id,
        "messages": messages,
        "truncated": truncated,
    }


def get_agent_trace(settings, agent_name, session_id, trace_key, max_spans=100):
    """Read one bounded trace for an exact-name agent session.

    This read-only operation matches ``agent_name`` exactly and
    case-sensitively. It returns at most 1,000 spans ordered by start time;
    prompt-bearing span attributes and non-error events are omitted.

    Args:
        settings: Validated MCP connection settings.
        agent_name: Exact, case-sensitive agent display name.
        session_id: Existing single-segment agent session identifier.
        trace_key: Existing single-segment trace identifier.
        max_spans: Maximum spans returned, from 1 through 1,000.

    Returns:
        dict: Sanitized trace metadata and ordered spans.

    Raises:
        AidpError: A selector or span bound is invalid.
    """
    agent_name = validate_resource_name(agent_name, "Agent")
    validate_resource_key(session_id)
    validate_resource_key(trace_key)
    _validate_result_limit(max_spans, MAX_TRACE_SPANS)
    with agent_clients(settings) as clients:
        instance_id, workspace_key, client = clients
        agent = _find_agent(client, instance_id, workspace_key, agent_name)
        trace = client.get_agent_session_trace(
            instance_id,
            workspace_key,
            resource_key(agent, "Agent"),
            session_id,
            trace_key,
        ).data
    spans = sorted(getattr(trace, "spans", None) or [], key=_time_sort_key)
    truncated = len(spans) > max_spans
    return {
        "trace_id": getattr(trace, "trace_id", None),
        "duration": _duration(trace),
        "spans": [_span_response(span) for span in spans[:max_spans]],
        "truncated": truncated,
    }


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


def _session_response(session):
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


def _message_text(message):
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


def _message_response(message, text):
    """Return message metadata, text, and metadata keys without metadata values."""
    metadata = getattr(message, "metadata", None)
    return {
        "role": getattr(message, "role", None),
        "time_created": getattr(message, "time_created", None),
        "tool_name": getattr(message, "tool_name", None),
        "text": text,
        "metadata_keys": sorted(metadata) if isinstance(metadata, dict) else [],
    }


def _time_sort_key(span):
    """Build a deterministic start-time ordering key for opaque SDK timestamps."""
    start_time = getattr(span, "start_time", None)
    if isinstance(start_time, (int, float)) and not isinstance(start_time, bool):
        return 0, start_time
    return 1, str(start_time)


def _duration(item):
    """Return a numeric duration when two compatible timestamps are available."""
    start_time = getattr(item, "start_time", None)
    end_time = getattr(item, "end_time", None)
    try:
        duration = end_time - start_time
    except (TypeError, ValueError):
        return None
    return duration.total_seconds() if hasattr(duration, "total_seconds") else duration


def _span_response(span):
    """Return a span without prompt-bearing attributes or non-error events."""
    return {
        "span_name": getattr(span, "span_name", None),
        "kind": getattr(span, "kind", None),
        "status": getattr(span, "status", None),
        "duration": _duration(span),
        "error_events": _error_events(getattr(span, "events", None) or []),
    }


def _error_events(events):
    """Keep bounded exception/error event names and messages only."""
    return [
        {"name": getattr(event, "name", None), "message": _error_message(event)}
        for event in events
        if _is_error_event(event)
    ]


def _is_error_event(event):
    """Recognize the standard exception/error event names without attributes."""
    name = getattr(event, "name", None)
    return isinstance(name, str) and (
        "error" in name.casefold() or "exception" in name.casefold()
    )


def _error_message(event):
    """Extract a bounded standard error message without returning attributes."""
    attributes = getattr(event, "attributes", None)
    if not isinstance(attributes, dict):
        return None
    for key in ("exception.message", "error.message", "message"):
        value = attributes.get(key)
        if isinstance(value, str):
            return value[:MAX_ERROR_EVENT_MESSAGE_CHARACTERS]
    return None


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
