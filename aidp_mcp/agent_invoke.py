"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Guarded OCI-signed invocation of deployed AI DP agents.
"""

import re
from urllib.parse import urlparse

import requests

from aidp_common.connection import AidpError, load_auth, validate_resource_key
from aidp_mcp.agent_lookup import find_agent, list_deployments
from aidp_mcp.lookups import resource_key
from aidp_mcp.safety import require_confirmation
from aidp_mcp.targets import agent_clients
from aidp_mcp.validation import _validate_result_limit, validate_resource_name

MAX_AGENT_MESSAGE_CHARACTERS = 20000
MAX_INVOKE_TIMEOUT_SECONDS = 600
MAX_MESSAGE_CHARACTERS = 100000
MAX_ERROR_RESPONSE_CHARACTERS = 1000
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
