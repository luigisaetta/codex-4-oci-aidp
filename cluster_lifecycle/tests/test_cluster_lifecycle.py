"""
Author: L. Saetta
Date last modified: 2026-09-15
License: MIT
Description: Exercise lifecycle actions through the installed Oracle AI DP SDK.
"""

import json
from unittest.mock import Mock

import oci
import pytest
from oci._vendor import requests

import cluster_lifecycle as lifecycle

TARGET = lifecycle.Target("instance", "workspace", "cluster")


@pytest.mark.parametrize(
    "action,origin,transition",
    [("start", "STOPPED", "STARTING"), ("stop", "ACTIVE", "STOPPING")],
)
def test_action_serialization(
    sdk_clients, http_response, capsys, *, action, origin, transition
):
    """Real SDK methods serialize the body, resource path, ETag and retry token."""
    sdk_clients.cluster_http.side_effect = [
        http_response({"state": origin}, etag="v1"),
        http_response({"state": transition}, 202, **{"aidp-async-operation-key": "op"}),
    ]
    lifecycle.change_state(sdk_clients.clusters, TARGET, action)
    calls = sdk_clients.cluster_http.call_args_list
    assert len(calls) == 2
    assert calls[0].args[0] == "GET"
    assert calls[1].args == (
        "POST",
        "https://datalake.eu-frankfurt-1.oci.oraclecloud.com/20260430/"
        "aiDataPlatforms/instance/workspaces/workspace/clusters/cluster/"
        f"actions/{action}",
    )
    assert json.loads(calls[1].kwargs["data"]) == {}
    assert calls[1].kwargs["headers"]["if-match"] == "v1"
    assert calls[1].kwargs["headers"]["opc-retry-token"]
    assert calls[1].kwargs["timeout"] == (10, 30)
    assert "completion has not yet been verified" in capsys.readouterr().out


@pytest.mark.parametrize(
    "action,state,dry_run",
    [
        ("status", "FAILED", False),
        ("start", "STOPPED", True),
        ("stop", "ACTIVE", True),
        ("start", "ACTIVE", False),
        ("stop", "STOPPED", False),
        ("start", "STARTING", False),
        ("stop", "STOPPING", False),
    ],
)
def test_read_only_and_noop(sdk_clients, http_response, action, state, dry_run):
    """Read-only, completed and in-progress operations never send POSTs."""
    sdk_clients.cluster_http.return_value = http_response({"state": state})
    lifecycle.change_state(sdk_clients.clusters, TARGET, action, dry_run=dry_run)
    assert sdk_clients.cluster_http.call_count == 1
    assert sdk_clients.cluster_http.call_args.args[0] == "GET"


@pytest.mark.parametrize(
    "state", ["FAILED", "DELETED", "STOPPING", "FUTURE_STATE", None]
)
def test_invalid_state(sdk_clients, http_response, state):
    """Reject unsafe, missing or future lifecycle states before mutation."""
    sdk_clients.cluster_http.return_value = http_response({"state": state})
    with pytest.raises(lifecycle.LifecycleError):
        lifecycle.change_state(sdk_clients.clusters, TARGET, "start")
    assert sdk_clients.cluster_http.call_count == 1


def test_wait_existing_operation(sdk_clients, http_response, capsys):
    """An existing transition can complete without another action request."""
    sdk_clients.cluster_http.side_effect = [
        http_response({"state": "STARTING"}),
        http_response({"state": "ACTIVE"}),
    ]
    lifecycle.change_state(sdk_clients.clusters, TARGET, "start", wait=True)
    assert all(c.args[0] == "GET" for c in sdk_clients.cluster_http.call_args_list)
    assert "Completed: ACTIVE" in capsys.readouterr().out


def test_wait_failure_and_timeout(sdk_clients, http_response, monkeypatch):
    """Polling failures and deadlines do not resubmit or cancel operations."""
    sdk_clients.cluster_http.side_effect = [
        http_response({"state": "STARTING"}),
        http_response({"state": "FAILED"}),
    ]
    with pytest.raises(lifecycle.LifecycleError, match="Unexpected cluster state"):
        lifecycle.change_state(sdk_clients.clusters, TARGET, "start", wait=True)
    sdk_clients.cluster_http.side_effect = None
    sdk_clients.cluster_http.return_value = http_response({"state": "STARTING"})
    monkeypatch.setattr(lifecycle.time, "monotonic", Mock(side_effect=[0, 2]))
    with pytest.raises(lifecycle.LifecycleError, match="not cancelled"):
        lifecycle.change_state(
            sdk_clients.clusters, TARGET, "start", wait=True, wait_timeout=1
        )


@pytest.mark.parametrize("status", [403, 409, 412, 429, 500])
def test_mutation_service_errors_are_not_retried(sdk_clients, http_response, status):
    """Each failed submission causes one POST, even for normally retryable errors."""
    sdk_clients.cluster_http.side_effect = [
        http_response({"state": "STOPPED"}),
        http_response({"code": "TestError", "message": "private server text"}, status),
    ]
    with pytest.raises(oci.exceptions.ServiceError):
        lifecycle.change_state(sdk_clients.clusters, TARGET, "start")
    assert sdk_clients.cluster_http.call_count == 2


def test_uncertain_submission(sdk_clients, http_response):
    """Transport errors are redacted and an unknown outcome is made explicit."""
    sdk_clients.cluster_http.side_effect = [
        http_response({"state": "STOPPED"}),
        requests.exceptions.ReadTimeout("SECRET"),
    ]
    with pytest.raises(lifecycle.LifecycleError, match="outcome is unknown") as error:
        lifecycle.change_state(sdk_clients.clusters, TARGET, "start")
    assert "SECRET" not in str(error.value)
    assert sdk_clients.cluster_http.call_count == 2


def test_sdk_encodes_cluster_identifiers(sdk_clients, http_response):
    """The SDK owns URL escaping for identifiers containing spaces."""
    sdk_clients.cluster_http.return_value = http_response({"state": "ACTIVE"})
    lifecycle.change_state(
        sdk_clients.clusters, lifecycle.Target("instance", "workspace", "c d"), "status"
    )
    assert (
        "/workspaces/workspace/clusters/c%20d"
        in sdk_clients.cluster_http.call_args.args[1]
    )


@pytest.mark.parametrize("key", ["a/b", "..", ".", "a\\b", "", " "])
def test_resource_keys_cannot_change_path(key):
    """Reject keys that the supported OCI SDK would interpret as path structure."""
    with pytest.raises(lifecycle.LifecycleError, match="single path segments"):
        lifecycle.Target("instance", key, "cluster")


def test_ai_compute_models(sdk_clients, http_response):
    """AI Compute responses deserialize through the SDK's discriminator."""
    sdk_clients.cluster_http.side_effect = [
        http_response({"sourceApi": "AI_COMPUTE", "state": "ACTIVE"}),
        http_response({"sourceApi": "AI_COMPUTE", "state": "STOPPING"}, 202),
    ]
    lifecycle.change_state(sdk_clients.clusters, TARGET, "stop")
    assert sdk_clients.cluster_http.call_count == 2


def test_sdk_region_and_override(sdk_clients):
    """Both generated clients resolve Frankfurt and accept an explicit origin."""
    for client in (sdk_clients.clusters, sdk_clients.workspaces):
        assert client.base_client.endpoint == (
            "https://datalake.eu-frankfurt-1.oci.oraclecloud.com/20260430"
        )
    custom = lifecycle.ClusterClient(
        {"region": "eu-frankfurt-1"},
        signer=sdk_clients.clusters.base_client.signer,
        service_endpoint="https://example.invalid",
    )
    try:
        assert custom.base_client.endpoint == "https://example.invalid/20260430"
    finally:
        custom.base_client.session.close()
