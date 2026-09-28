"""
Author: L. Saetta
Date last modified: 2026-09-28
License: MIT
Description: Bounded, redacted observation of AI DP asynchronous operations.
"""

from datetime import datetime
import re

from aidp_common.connection import AidpError
from aidp_mcp.agent_lookup import collection_items, json_value
from aidp_mcp.lookups import SDK_PAGE_SIZE, next_page, resource_key
from aidp_mcp.targets import async_operations_clients
from aidp_mcp.validation import _validate_result_limit

MAX_ASYNC_OPERATION_RESULTS = 1000
MAX_FIELD_CHARACTERS = 1000
RESOURCE_TYPES = {"AGENT", "AI_COMPUTE", "CLUSTER"}
STATUSES = {"ACCEPTED", "IN_PROGRESS", "SUCCEEDED", "FAILED", "CANCELED"}
OCID_PATTERN = re.compile(
    r"ocid1\.[a-z0-9_-]+\.[a-z0-9_-]*\.[a-z0-9_-]*\.[A-Za-z0-9._-]+"
)


def list_async_operations(
    settings, resource_type, *, status=None, name_contains=None, max_results=25
):
    """AI DP async operations: list bounded, redacted instance operations.

    Results are newest-first. The resource type is restricted to the values
    verified with the service; a local name filter is case-sensitive and never
    sent to AI DP.

    Args:
        settings: Validated MCP connection settings.
        resource_type: One supported AI DP async-operation resource type.
        status: Optional verified service status filter.
        name_contains: Optional case-sensitive display-name substring.
        max_results: Maximum operations returned, from 1 through 1,000.

    Returns:
        dict: Sanitized operation summaries and a truncation indicator.

    Raises:
        AidpError: A filter, result limit, or returned operation key is invalid.
    """
    _validate_resource_type(resource_type)
    _validate_status(status)
    _validate_name_contains(name_contains)
    _validate_result_limit(max_results, MAX_ASYNC_OPERATION_RESULTS)

    operations = []
    page = None
    truncated = False
    with async_operations_clients(settings) as clients:
        instance_id, client = clients
        while len(operations) < max_results:
            response = client.list_async_operations(
                instance_id,
                resource_type=resource_type,
                status=status,
                limit=min(SDK_PAGE_SIZE, max_results - len(operations)),
                page=page,
                sort_by="timeStarted",
                sort_order="DESC",
            )
            items = collection_items(response)
            for index, operation in enumerate(items):
                display_name = getattr(operation, "resource_display_name", None)
                if name_contains is not None and name_contains not in (
                    display_name or ""
                ):
                    continue
                operation_key = resource_key(operation, "Async operation")
                detail = client.get_async_operation(instance_id, operation_key).data
                operations.append(_operation_response(detail))
                if len(operations) == max_results:
                    truncated = index < len(items) - 1
                    break
            page = next_page(response)
            if not page:
                break
            truncated = truncated or len(operations) == max_results
    return {"operations": operations, "truncated": truncated}


def _validate_resource_type(resource_type):
    """Reject resource types not verified for this service endpoint."""
    if resource_type not in RESOURCE_TYPES:
        values = ", ".join(sorted(RESOURCE_TYPES))
        raise AidpError(f"resource_type must be one of: {values}.")


def _validate_status(status):
    """Reject status filters not verified for this service endpoint."""
    if status is not None and status not in STATUSES:
        values = ", ".join(sorted(STATUSES))
        raise AidpError(f"status must be one of: {values}.")


def _validate_name_contains(name_contains):
    """Require a useful local display-name filter when one is supplied."""
    if name_contains is not None and (
        not isinstance(name_contains, str) or not name_contains
    ):
        raise AidpError("name_contains must be a nonempty string when provided.")


def _operation_response(operation):
    """Return selected, bounded detail fields without creator identifiers."""
    return {
        "key": _safe_value(resource_key(operation, "Async operation")),
        "resource_type": _safe_value(getattr(operation, "resource_type", None)),
        "action_type": _safe_value(getattr(operation, "action_type", None)),
        "resource_display_name": _safe_value(
            getattr(operation, "resource_display_name", None)
        ),
        "created_by_name": _safe_value(getattr(operation, "created_by_name", None)),
        "time_started": _safe_value(getattr(operation, "time_started", None)),
        "time_finished": _safe_value(getattr(operation, "time_finished", None)),
        "duration_s": _duration_seconds(operation),
        "status": _safe_value(getattr(operation, "status", None)),
        "status_details": _safe_value(getattr(operation, "status_details", None)),
        "error_code": _safe_value(getattr(operation, "error_code", None)),
        "error_message": _safe_value(getattr(operation, "error_message", None)),
    }


def _safe_value(value):
    """Convert a scalar to JSON-safe bounded text while masking OCIDs."""
    value = json_value(value)
    if not isinstance(value, str):
        return value
    return OCID_PATTERN.sub("<ocid>", value)[:MAX_FIELD_CHARACTERS]


def _duration_seconds(operation):
    """Return the finished-operation duration in seconds when calculable."""
    start_time = getattr(operation, "time_started", None)
    finish_time = getattr(operation, "time_finished", None)
    if not isinstance(start_time, datetime) or not isinstance(finish_time, datetime):
        return None
    return float((finish_time - start_time).total_seconds())
