"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Read-only AI DP agent discovery and deployment observation.
"""

from urllib.parse import urlparse

import requests

from aidp_common.connection import AidpError, load_auth, validate_resource_key
from aidp_mcp.lookups import SDK_PAGE_SIZE, next_page, resource_key
from aidp_mcp.safety import require_confirmation
from aidp_mcp.targets import agent_clients
from aidp_mcp.validation import _validate_result_limit, validate_resource_name

MAX_AGENT_RESULTS = 1000
MAX_AGENT_DEPLOYMENTS = 1000
MAX_AGENT_SESSIONS = 1000
MAX_AGENT_MESSAGES = 1000
MAX_MESSAGE_CHARACTERS = 100000
MAX_TRACE_SPANS = 1000
MAX_ERROR_EVENT_MESSAGE_CHARACTERS = 1000
MAX_AGENT_MESSAGE_CHARACTERS = 20000
MAX_INVOKE_TIMEOUT_SECONDS = 600
MAX_ERROR_RESPONSE_CHARACTERS = 1000


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
                limit=min(SDK_PAGE_SIZE, max_results - len(agents)),
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
                limit=min(SDK_PAGE_SIZE, max_results - len(sessions)),
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
                limit=min(SDK_PAGE_SIZE, MAX_AGENT_MESSAGES - len(messages)),
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


def invoke_agent(
    settings,
    agent_name,
    message,
    *,
    session_key=None,
    timeout_seconds=120,
    max_characters=12000,
    confirm_invoke=False,
):
    """Send one message to the active deployment of an exact-name agent.

    Invocation creates a platform session and can consume compute or trigger
    agent tools. It requires explicit confirmation and never accepts a caller
    supplied endpoint. Returned text is bounded to 100,000 characters.

    Args:
        settings: Validated MCP connection settings.
        agent_name: Exact, case-sensitive agent display name.
        message: Nonempty user message up to 20,000 characters.
        session_key: Optional existing single-segment session identifier.
        timeout_seconds: HTTP timeout from 1 through 600 seconds.
        max_characters: Returned text limit from 1 through 100,000.
        confirm_invoke: Must be exactly ``True`` before a remote call.

    Returns:
        dict: Bounded response metadata and extracted text.

    Raises:
        AidpError: Confirmation, input, deployment, transport, or response
            validation fails.
    """
    require_confirmation(
        confirm_invoke,
        "Set confirm_invoke=true to send a message to a deployed agent.",
    )
    agent_name = validate_resource_name(agent_name, "Agent")
    _validate_agent_message(message)
    if session_key is not None:
        validate_resource_key(session_key)
    _validate_invoke_timeout(timeout_seconds)
    _validate_result_limit(max_characters, MAX_MESSAGE_CHARACTERS)

    with agent_clients(settings) as clients:
        instance_id, workspace_key, client = clients
        agent = _find_agent(client, instance_id, workspace_key, agent_name)
        deployments, _ = _list_deployments(
            client, instance_id, workspace_key, resource_key(agent, "Agent")
        )
    endpoint = _active_chat_endpoint(deployments)
    _, options = load_auth(settings)
    body = _invoke_request_body(message, session_key)
    response = _post_agent_message(endpoint, options["signer"], body, timeout_seconds)
    if not 200 <= response.status_code < 300:
        raise AidpError(
            "Agent invocation returned HTTP "
            f"{response.status_code}: {_response_excerpt(response)}"
        )
    response_body = _json_response(response)
    text, truncated = _response_text(response_body, max_characters)
    response_session_key = _response_session_key(response_body, response.headers)
    return {
        "agent_name": agent_name,
        "http_status": response.status_code,
        "response_id": _response_id(response_body),
        "session_key": response_session_key,
        "session_key_note": (
            None
            if response_session_key is not None
            else "No session key was found in the response body or headers."
        ),
        "text": text,
        "truncated": truncated,
        "response_keys": sorted(response_body),
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
            limit=min(SDK_PAGE_SIZE, 2 - len(matches)),
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
            limit=min(SDK_PAGE_SIZE, MAX_AGENT_DEPLOYMENTS - len(deployments)),
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


def _validate_agent_message(message):
    """Validate a bounded nonempty message before remote agent lookup."""
    if (
        not isinstance(message, str)
        or not message.strip()
        or len(message) > MAX_AGENT_MESSAGE_CHARACTERS
    ):
        raise AidpError("message must be a nonempty string up to 20,000 characters.")


def _validate_invoke_timeout(timeout_seconds):
    """Validate the bounded no-retry HTTP timeout."""
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or not 1 <= timeout_seconds <= MAX_INVOKE_TIMEOUT_SECONDS
    ):
        raise AidpError("timeout_seconds must be an integer from 1 through 600.")


def _active_chat_endpoint(deployments):
    """Select exactly one active deployment and build its validated chat URL."""
    states = [deployment.get("lifecycle_state") for deployment in deployments]
    active = [
        deployment
        for deployment in deployments
        if deployment.get("lifecycle_state") == "ACTIVE"
    ]
    if len(active) != 1:
        raise AidpError(
            "Expected exactly one ACTIVE agent deployment; "
            f"found {len(active)} among states {states}."
        )
    endpoint_url = active[0].get("endpoint_url")
    if not isinstance(endpoint_url, str) or not endpoint_url:
        raise AidpError("The ACTIVE agent deployment did not provide an endpoint URL.")
    parsed = urlparse(endpoint_url)
    hostname = parsed.hostname
    if not _is_valid_agent_endpoint(parsed, hostname):
        raise AidpError(
            "The ACTIVE agent deployment returned an invalid Oracle HTTPS endpoint."
        )
    return f"{endpoint_url.rstrip('/')}/chat"


def _is_valid_agent_endpoint(parsed, hostname):
    """Return whether a parsed deployment URL is a clean Oracle HTTPS URL."""
    has_oracle_host = hostname and hostname.endswith(".oraclecloud.com")
    has_disallowed_components = any(
        (
            parsed.username is not None,
            parsed.password is not None,
            parsed.query,
            parsed.fragment,
        )
    )
    return (
        parsed.scheme == "https" and has_oracle_host and not has_disallowed_components
    )


def _invoke_request_body(message, session_key):
    """Build the documented non-streaming chat request without metadata."""
    body = {
        "isStreamEnabled": False,
        "input": [
            {
                "role": "User",
                "content": [{"type": "INPUT_TEXT", "text": message}],
            }
        ],
    }
    if session_key is not None:
        body["sessionKey"] = session_key
    return body


def _post_agent_message(endpoint, signer, body, timeout_seconds):
    """Send one signed, non-redirected request without retries."""
    session = requests.Session()
    try:
        return session.post(
            endpoint,
            auth=signer,
            json=body,
            timeout=timeout_seconds,
            allow_redirects=False,
        )
    except requests.Timeout as error:
        raise AidpError(
            "Agent invocation timed out; no retry was attempted."
        ) from error
    except requests.ConnectionError as error:
        raise AidpError(
            "Could not connect to the agent endpoint; no retry was attempted."
        ) from error
    except requests.RequestException as error:
        raise AidpError(
            "Agent invocation request failed; no retry was attempted."
        ) from error
    finally:
        session.close()


def _response_excerpt(response):
    """Return a bounded response body excerpt without request details."""
    text = getattr(response, "text", "")
    return text[:MAX_ERROR_RESPONSE_CHARACTERS] if isinstance(text, str) else ""


def _json_response(response):
    """Return an object JSON response or raise an actionable sanitized error."""
    try:
        body = response.json()
    except ValueError as error:
        raise AidpError(
            "Agent invocation returned a non-JSON success response."
        ) from error
    if not isinstance(body, dict):
        raise AidpError(
            "Agent invocation returned a JSON response that is not an object."
        )
    return body


def _response_id(response_body):
    """Find a documented-or-observed top-level response identifier."""
    for key in ("responseId", "response_id", "id"):
        value = response_body.get(key)
        if isinstance(value, str):
            return value
    return None


def _response_session_key(response_body, headers):
    """Find a session key in observed top-level fields or response headers."""
    for key in ("sessionKey", "session_key"):
        value = response_body.get(key)
        if isinstance(value, str):
            return value
    for key, value in headers.items():
        if key.casefold() in {"sessionkey", "session-key", "x-session-key"}:
            return value if isinstance(value, str) else None
    return None


def _response_text(response_body, max_characters):
    """Concatenate bounded text values from the documented response output."""
    text_parts = []
    for output in response_body.get("output", []):
        if not isinstance(output, dict):
            continue
        for content in output.get("content", []):
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                text_parts.append(content["text"])
    text = "".join(text_parts)
    return text[:max_characters], len(text) > max_characters


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
