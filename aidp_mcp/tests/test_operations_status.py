"""
Author: L. Saetta
Date last modified: 2026-09-28
License: MIT
Description: Offline tests for AI DP MCP asynchronous-operation observation.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from aidp_python_client.aidataplatform_dp import models

from aidp_common.connection import AidpError
from aidp_mcp import operations_status
from aidp_mcp.tests.agent_fixtures import collection_response as _response


@contextmanager
def _clients(client):
    """Provide a minimal async-operations client context for one test."""
    yield "instance", client


def _operation(key="operation-key", **values):
    """Build a representative async-operation summary or detail fixture."""
    defaults = {
        "key": key,
        "resource_type": "AGENT",
        "action_type": "DEPLOY_AGENT",
        "resource_display_name": "agent-key_PROD_1",
        "created_by": "ocid1.user.oc1..private",
        "created_by_name": "Operator",
        "time_started": datetime(2026, 9, 28, 8, 0, tzinfo=timezone.utc),
        "time_finished": datetime(2026, 9, 28, 8, 0, 38, tzinfo=timezone.utc),
        "status": "SUCCEEDED",
        "status_details": "Completed ocid1.instance.oc1..private",
        "error_code": None,
        "error_message": None,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


def test_list_async_operations_returns_bounded_redacted_details(monkeypatch):
    """The tool requests newest-first pages and hides creator identifiers."""
    client = Mock()
    first = _operation("first")
    second = _operation("second", resource_display_name="other")
    client.list_async_operations.return_value = _response([first, second], "later")
    client.get_async_operation.side_effect = [
        SimpleNamespace(data=first),
        SimpleNamespace(data=second),
    ]
    monkeypatch.setattr(
        operations_status,
        "async_operations_clients",
        lambda _settings: _clients(client),
    )

    result = operations_status.list_async_operations(
        SimpleNamespace(), "AGENT", name_contains="agent-key", max_results=1
    )

    assert result == {
        "operations": [
            {
                "key": "first",
                "resource_type": "AGENT",
                "action_type": "DEPLOY_AGENT",
                "resource_display_name": "agent-key_PROD_1",
                "created_by_name": "Operator",
                "time_started": "2026-09-28T08:00:00+00:00",
                "time_finished": "2026-09-28T08:00:38+00:00",
                "duration_s": 38.0,
                "status": "SUCCEEDED",
                "status_details": "Completed <ocid>",
                "error_code": None,
                "error_message": None,
            }
        ],
        "truncated": True,
    }
    client.list_async_operations.assert_called_once_with(
        "instance",
        resource_type="AGENT",
        status=None,
        limit=1,
        page=None,
        sort_by="timeStarted",
        sort_order="DESC",
    )
    client.get_async_operation.assert_called_once_with("instance", "first")


def test_list_async_operations_filters_before_detail_reads(monkeypatch):
    """A local name filter never retrieves excluded operation details."""
    client = Mock()
    match = _operation("match")
    excluded = _operation("excluded", resource_display_name="different")
    client.list_async_operations.return_value = _response([excluded, match])
    client.get_async_operation.return_value = SimpleNamespace(data=match)
    monkeypatch.setattr(
        operations_status,
        "async_operations_clients",
        lambda _settings: _clients(client),
    )

    result = operations_status.list_async_operations(
        SimpleNamespace(), "AGENT", status="SUCCEEDED", name_contains="agent-key"
    )

    assert [item["key"] for item in result["operations"]] == ["match"]
    client.get_async_operation.assert_called_once_with("instance", "match")
    assert client.list_async_operations.call_args.kwargs["status"] == "SUCCEEDED"


def test_list_async_operations_bounds_and_masks_detail_errors(monkeypatch):
    """Detail-only failures are bounded and cannot expose embedded OCIDs."""
    client = Mock()
    operation = _operation(
        error_code="FAILED",
        error_message="ocid1.user.oc1..private " + "x" * 1200,
        time_finished=None,
    )
    client.list_async_operations.return_value = _response([operation])
    client.get_async_operation.return_value = SimpleNamespace(data=operation)
    monkeypatch.setattr(
        operations_status,
        "async_operations_clients",
        lambda _settings: _clients(client),
    )

    result = operations_status.list_async_operations(SimpleNamespace(), "AGENT")

    item = result["operations"][0]
    assert item["duration_s"] is None
    assert item["error_message"].startswith("<ocid> ")
    assert len(item["error_message"]) == 1000


@pytest.mark.parametrize(
    ("resource_type", "status", "name_contains", "max_results"),
    [
        ("UNKNOWN", None, None, 25),
        ("AGENT", "UNKNOWN", None, 25),
        ("AGENT", None, "", 25),
        ("AGENT", None, None, 0),
    ],
)
def test_list_async_operations_validates_before_client_creation(
    monkeypatch, resource_type, status, name_contains, max_results
):
    """Unsupported filters and bounds fail locally without a service call."""
    client_context = Mock()
    monkeypatch.setattr(operations_status, "async_operations_clients", client_context)

    with pytest.raises(AidpError):
        operations_status.list_async_operations(
            SimpleNamespace(),
            resource_type,
            status=status,
            name_contains=name_contains,
            max_results=max_results,
        )

    client_context.assert_not_called()


def test_list_async_operations_caps_every_sdk_page_at_one_hundred(monkeypatch):
    """The shared SDK page-size limit applies to asynchronous operations."""
    client = Mock()
    client.list_async_operations.return_value = _response([])
    monkeypatch.setattr(
        operations_status,
        "async_operations_clients",
        lambda _settings: _clients(client),
    )

    operations_status.list_async_operations(
        SimpleNamespace(), "AI_COMPUTE", max_results=1000
    )

    assert client.list_async_operations.call_args.kwargs["limit"] == 100


def test_list_async_operations_serializes_real_sdk_models(monkeypatch):
    """Generated SDK detail objects never escape the JSON-safe response."""
    client = Mock()
    operation = models.AsyncOperation(
        key="operation-key",
        resource_type="AGENT",
        action_type="DEPLOY_AGENT",
        resource_name="agent-key",
        resource_display_name="agent-key_PROD_1",
        created_by="ocid1.user.oc1..private",
        created_by_name="Operator",
        time_started=datetime(2026, 9, 28, 8, 0, tzinfo=timezone.utc),
        time_finished=datetime(2026, 9, 28, 8, 0, 38, tzinfo=timezone.utc),
        status="SUCCEEDED",
        status_details="Completed",
    )
    client.list_async_operations.return_value = _response([operation])
    client.get_async_operation.return_value = SimpleNamespace(data=operation)
    monkeypatch.setattr(
        operations_status,
        "async_operations_clients",
        lambda _settings: _clients(client),
    )

    result = operations_status.list_async_operations(SimpleNamespace(), "AGENT")

    json.dumps(result)
