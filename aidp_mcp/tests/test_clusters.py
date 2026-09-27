"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Offline tests for AI DP MCP cluster lifecycle operations.
"""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aidp_common.connection import AidpError
from aidp_mcp import clusters


def test_cluster_response_omits_sensitive_runtime_references():
    """Cluster status returns selected configuration rather than SDK internals."""
    cluster = SimpleNamespace(
        key="cluster-key",
        display_name="clu02",
        type="USER",
        state="ACTIVE",
        state_details="Ready",
        cluster_runtime_config=SimpleNamespace(runtime_version="3.5"),
        node_type="FLEX",
        driver_config=SimpleNamespace(
            driver_node_type="FLEX",
            driver_shape="VM.Standard.E5.Flex",
            driver_shape_config=SimpleNamespace(ocpus=2, memory_in_gbs=32),
        ),
        worker_config=SimpleNamespace(
            worker_shape="VM.Standard.E5.Flex",
            worker_shape_config=SimpleNamespace(ocpus=2, memory_in_gbs=32),
            min_worker_count=1,
            max_worker_count=3,
        ),
        auto_termination_minutes=30,
        jdbc_endpoint_url="sensitive-endpoint",
        log_group_id="sensitive-log-group",
    )

    result = getattr(clusters, "_cluster_response")(cluster)

    assert result == {
        "cluster_key": "cluster-key",
        "display_name": "clu02",
        "type": "USER",
        "state": "ACTIVE",
        "state_details": "Ready",
        "runtime_version": "3.5",
        "node_type": "FLEX",
        "driver": {
            "node_type": "FLEX",
            "shape": "VM.Standard.E5.Flex",
            "ocpus": 2,
            "memory_in_gbs": 32,
        },
        "workers": {
            "shape": "VM.Standard.E5.Flex",
            "ocpus": 2,
            "memory_in_gbs": 32,
            "min_worker_count": 1,
            "max_worker_count": 3,
        },
        "auto_termination_minutes": 30,
    }


def test_set_cluster_state_starts_stopped_cluster_with_etag(monkeypatch):
    """Lifecycle start uses one ETag-guarded, no-retry SDK submission."""
    cluster_client = Mock()
    cluster = SimpleNamespace(key="cluster-key", display_name="clu02", state="STOPPED")
    settings = SimpleNamespace()

    @contextmanager
    def clients():
        yield "instance", "workspace", cluster_client, Mock(), Mock()

    monkeypatch.setattr(clusters, "workspace_clients", lambda _settings: clients())
    monkeypatch.setattr(
        clusters,
        "find_cluster_details",
        Mock(return_value=SimpleNamespace(data=cluster, headers={"etag": "etag"})),
    )
    cluster_client.start_cluster.return_value = SimpleNamespace(status=202)

    result = clusters.set_cluster_state(settings, "clu02", "start", confirm_action=True)

    assert result["outcome"] == "accepted"
    assert result["cluster"]["state"] == "STOPPED"
    arguments = cluster_client.start_cluster.call_args
    assert arguments.args[:3] == ("instance", "workspace", "cluster-key")
    assert type(arguments.args[3]).__name__ == "StartClusterDetails"
    assert arguments.kwargs["if_match"] == "etag"
    assert type(arguments.kwargs["retry_strategy"]).__name__ == "NoneRetryStrategy"
    assert arguments.kwargs["opc_retry_token"]


def test_set_cluster_state_requires_explicit_confirmation():
    """The lifecycle tool never performs discovery or mutation without consent."""
    settings = SimpleNamespace()

    with pytest.raises(AidpError, match="confirm_action=true"):
        clusters.set_cluster_state(settings, "clu02", "start")


def test_set_cluster_state_does_not_resubmit_active_start(monkeypatch):
    """An already active cluster is a successful lifecycle no-op."""
    cluster_client = Mock()
    cluster = SimpleNamespace(key="cluster-key", display_name="clu02", state="ACTIVE")
    settings = SimpleNamespace()

    @contextmanager
    def clients():
        yield "instance", "workspace", cluster_client, Mock(), Mock()

    monkeypatch.setattr(clusters, "workspace_clients", lambda _settings: clients())
    monkeypatch.setattr(
        clusters,
        "find_cluster_details",
        Mock(return_value=SimpleNamespace(data=cluster, headers={})),
    )

    result = clusters.set_cluster_state(settings, "clu02", "start", confirm_action=True)

    assert result["outcome"] == "already_desired"
    cluster_client.start_cluster.assert_not_called()


def test_set_cluster_state_stops_active_cluster(monkeypatch):
    """Lifecycle stop selects the typed stop request for an active cluster."""
    cluster_client = Mock()
    cluster = SimpleNamespace(key="cluster-key", display_name="clu02", state="ACTIVE")
    settings = SimpleNamespace()

    @contextmanager
    def clients():
        yield "instance", "workspace", cluster_client, Mock(), Mock()

    monkeypatch.setattr(clusters, "workspace_clients", lambda _settings: clients())
    monkeypatch.setattr(
        clusters,
        "find_cluster_details",
        Mock(return_value=SimpleNamespace(data=cluster, headers={})),
    )
    cluster_client.stop_cluster.return_value = SimpleNamespace(status=202)

    result = clusters.set_cluster_state(settings, "clu02", "stop", confirm_action=True)

    assert result["outcome"] == "accepted"
    assert (
        type(cluster_client.stop_cluster.call_args.args[3]).__name__
        == "StopClusterDetails"
    )


@pytest.mark.parametrize("value", [0, True, "1200"])
def test_set_cluster_state_rejects_unsafe_wait_limits(value):
    """Cluster lifecycle polling bounds are validated before cloud discovery."""
    settings = SimpleNamespace()

    with pytest.raises(AidpError, match="timeout_seconds"):
        clusters.set_cluster_state(
            settings, "clu02", "start", timeout_seconds=value, confirm_action=True
        )
