"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Read-only AI DP agent observation and session inspection.
"""

from aidp_common.connection import AidpError, validate_resource_key
from aidp_mcp.agent_lookup import (
    agent_response,
    collection_items,
    duration_milliseconds,
    find_agent,
    get_agent_response,
    message_response,
    message_text,
    session_response,
    span_response,
    time_sort_key,
)
from aidp_mcp.lookups import SDK_PAGE_SIZE, next_page, resource_key
from aidp_mcp.targets import agent_clients
from aidp_mcp.validation import _validate_result_limit, validate_resource_name

MAX_AGENT_RESULTS = 1000
MAX_AGENT_SESSIONS = 1000
MAX_AGENT_MESSAGES = 1000
MAX_MESSAGE_CHARACTERS = 100000
MAX_TRACE_SPANS = 1000


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
    agent_items = []
    page = None
    truncated = False
    with agent_clients(settings) as clients:
        instance_id, workspace_key, client = clients
        while len(agent_items) < max_results:
            response = client.list_agents(
                instance_id,
                workspace_key,
                display_name_contains=name_contains,
                limit=min(SDK_PAGE_SIZE, max_results - len(agent_items)),
                page=page,
            )
            items = collection_items(response)
            for index, agent in enumerate(items):
                agent_items.append(agent_response(agent))
                if len(agent_items) == max_results:
                    truncated = index < len(items) - 1
                    break
            page, truncated = _agent_page_state(response, truncated)
            if not page:
                break
    return {"agents": agent_items, "truncated": truncated}


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
        return get_agent_response(client, instance_id, workspace_key, agent_name)


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
        agent = find_agent(client, instance_id, workspace_key, agent_name)
        agent_key = resource_key(agent, "Agent")
        while len(sessions) < max_results:
            response = client.list_agent_sessions(
                instance_id,
                workspace_key,
                agent_key,
                limit=min(SDK_PAGE_SIZE, max_results - len(sessions)),
                page=page,
                sort_by="timeCreated",
                sort_order="DESC",
            )
            items = collection_items(response)
            for index, session in enumerate(items):
                sessions.append(session_response(session))
                if len(sessions) == max_results:
                    truncated = index < len(items) - 1
                    break
            page, truncated = _agent_page_state(response, truncated)
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
        agent = find_agent(client, instance_id, workspace_key, agent_name)
        agent_key = resource_key(agent, "Agent")
        while len(messages) < MAX_AGENT_MESSAGES and not truncated:
            response = client.list_agent_session_chat_histories(
                instance_id,
                workspace_key,
                agent_key,
                session_id,
                limit=min(SDK_PAGE_SIZE, MAX_AGENT_MESSAGES - len(messages)),
                page=page,
            )
            items = collection_items(response)
            for index, message in enumerate(items):
                text = message_text(message)
                remaining = max_characters - used_characters
                if len(text) > remaining:
                    text = text[:remaining]
                    truncated = True
                used_characters += len(text)
                messages.append(message_response(message, text))
                if truncated or len(messages) == MAX_AGENT_MESSAGES:
                    truncated = truncated or index < len(items) - 1
                    break
            page, truncated = _agent_page_state(response, truncated)
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
        agent = find_agent(client, instance_id, workspace_key, agent_name)
        trace = client.get_agent_session_trace(
            instance_id,
            workspace_key,
            resource_key(agent, "Agent"),
            session_id,
            trace_key,
        ).data
    spans = sorted(getattr(trace, "spans", None) or [], key=time_sort_key)
    truncated = len(spans) > max_spans
    return {
        "trace_id": getattr(trace, "trace_id", None),
        "duration_ms": duration_milliseconds(trace),
        "spans": [span_response(span) for span in spans[:max_spans]],
        "truncated": truncated,
    }


def _agent_page_state(response, truncated):
    """Return the next page and preserve a prior or newly observed truncation."""
    next_token = next_page(response)
    return next_token, truncated or bool(next_token)
