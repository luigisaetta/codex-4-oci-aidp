"""
Author: L. Saetta
Date last modified: 2026-09-28
License: MIT
Description: Guarded OCI-signed invocation of deployed AI DP agents.
"""

import re
from urllib.parse import urlparse

import requests

from aidp_common.connection import AidpError, load_auth, validate_resource_key
from aidp_mcp.agent_lookup import (
    NANOSECONDS_PER_MILLISECOND,
    find_agent,
    list_deployments,
    span_kind,
)
from aidp_mcp.lookups import resource_key
from aidp_mcp.safety import require_confirmation
from aidp_mcp.targets import agent_clients
from aidp_mcp.validation import _validate_result_limit, validate_resource_name

MAX_AGENT_MESSAGE_CHARACTERS = 20000
MAX_INVOKE_TIMEOUT_SECONDS = 600
MAX_MESSAGE_CHARACTERS = 100000
MAX_ERROR_RESPONSE_CHARACTERS = 1000
MAX_AGENT_ERROR_CHARACTERS = 1000
MAX_TRACE_SPANS = 1000
OCID_PATTERN = re.compile(
    r"ocid1\.[a-z0-9_-]+\.[a-z0-9_-]*\.[a-z0-9_-]*\.[A-Za-z0-9._-]+"
)


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
        agent = find_agent(client, instance_id, workspace_key, agent_name)
        deployments, _ = list_deployments(
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
    trace = _inline_trace(response_body)
    response_session_key = _trace_session_id(trace) or _response_session_key(
        response_body, response.headers
    )
    result = {
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
        "trace_id": _trace_id(trace),
        "session_id": _trace_session_id(trace),
        "trace_summary": _trace_summary(trace),
        "usage": _usage(response_body),
    }
    agent_error = _agent_error(response_body)
    if agent_error is not None:
        result["agent_error"] = agent_error
    return result


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
    return _chat_url(endpoint_url, parsed.path)


def _chat_url(endpoint_url, path):
    """Return a supported deployment chat URL without duplicating ``/chat``."""
    if path.endswith("/chat"):
        return endpoint_url
    if re.fullmatch(r".*/agentendpoint/[^/]+", path):
        return f"{endpoint_url}/chat"
    raise AidpError(
        "The ACTIVE agent deployment endpoint path must end with '/chat' or "
        "'/agentendpoint/<agent-key>'; the deployment returned an unsupported path."
    )


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
    """Return a bounded response body excerpt without request details or OCIDs."""
    text = getattr(response, "text", "")
    if not isinstance(text, str):
        return ""
    return OCID_PATTERN.sub("<ocid>", text)[:MAX_ERROR_RESPONSE_CHARACTERS]


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
    """Extract only observed ``output_text`` content, excluding trace items."""
    text_parts = []
    for output in response_body.get("output", []):
        if not isinstance(output, dict):
            continue
        for content in output.get("content", []):
            if (
                isinstance(content, dict)
                and content.get("type") == "output_text"
                and isinstance(content.get("text"), str)
            ):
                text_parts.append(content["text"])
    text = "".join(text_parts)
    return text[:max_characters], len(text) > max_characters


def _inline_trace(response_body):
    """Return ``output_text.traces`` from the verified deployed-agent shape."""
    for output in response_body.get("output", []):
        if not isinstance(output, dict):
            continue
        for content in output.get("content", []):
            if not isinstance(content, dict):
                continue
            trace = (
                content.get("traces") if content.get("type") == "output_text" else None
            )
            if isinstance(trace, dict):
                return trace
    return None


def _trace_id(trace):
    """Extract a trace identifier from the observed camel- or snake-case key."""
    if not isinstance(trace, dict):
        return None
    value = trace.get("id", trace.get("traceId"))
    return value if isinstance(value, str) else None


def _trace_session_id(trace):
    """Extract the session identifier shared by the session observation tools."""
    if not isinstance(trace, dict):
        return None
    value = trace.get("parentSessionId", trace.get("parent_session_id"))
    return value if isinstance(value, str) else None


def _trace_summary(trace):
    """Return bounded span metadata without attributes or non-error events."""
    if not isinstance(trace, dict):
        return None
    spans = trace.get("spans")
    if not isinstance(spans, list):
        return []
    return [
        _trace_span_summary(span)
        for span in spans[:MAX_TRACE_SPANS]
        if isinstance(span, dict)
    ]


def _trace_span_summary(span):
    """Sanitize one response trace span without its attributes or events."""
    return {
        "span_name": _bounded_string(span.get("spanName", span.get("span_name"))),
        "kind": _trace_kind_name(span.get("kind")),
        "status": _trace_status(span.get("status")),
        "duration_ms": _trace_span_duration_milliseconds(span),
    }


def _trace_status(status):
    """Return bounded JSON-safe response trace status without arbitrary data."""
    if isinstance(status, dict):
        return {
            "code": _bounded_string(status.get("code")),
            "message": _bounded_string(status.get("message")),
        }
    if isinstance(status, str):
        return {"code": _bounded_string(status), "message": None}
    return None


def _trace_kind_name(kind):
    """Normalize numeric response trace kinds to their documented names."""
    return (
        span_kind(kind)
        if isinstance(kind, (str, int)) and not isinstance(kind, bool)
        else None
    )


def _trace_span_duration_milliseconds(span):
    """Convert an inline-trace span's nanosecond timestamps to milliseconds."""
    start_time = span.get("startTime", span.get("start_time"))
    end_time = span.get("endTime", span.get("end_time"))
    try:
        return float((end_time - start_time) / NANOSECONDS_PER_MILLISECOND)
    except (TypeError, ValueError):
        return None


def _usage(response_body):
    """Return the three known token counts without exposing other usage data."""
    usage = response_body.get("usage")
    if not isinstance(usage, dict):
        return None
    return {
        "input_tokens": _token_count(usage.get("inputTokens")),
        "output_tokens": _token_count(usage.get("outputTokens")),
        "total_tokens": _token_count(usage.get("totalTokens")),
    }


def _token_count(value):
    """Return a non-Boolean nonnegative token count, or ``None``."""
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else None
    )


def _agent_error(response_body):
    """Return a bounded agent error only when the response reports a code."""
    error = response_body.get("error")
    if not isinstance(error, dict):
        return None
    code = _bounded_string(error.get("code"))
    if not code:
        return None
    return {"code": code, "message": _bounded_string(error.get("message"))}


def _bounded_string(value):
    """Return a bounded string without coercing arbitrary response values."""
    return value[:MAX_AGENT_ERROR_CHARACTERS] if isinstance(value, str) else None
