"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: AI DP cluster status, lifecycle submission, and bounded polling.
"""

import time
from uuid import uuid4

import oci
from aidp_python_client.aidataplatform_dp import models

from aidp_common.connection import AidpError
from aidp_mcp.lookups import find_cluster_details, find_cluster_status, resource_key
from aidp_mcp.safety import require_confirmation
from aidp_mcp.targets import workspace_clients


def get_cluster_status(settings, cluster_name):
    """Read a sanitized summary for one exact configured-workspace cluster.

    Args:
        cluster_name: Exact cluster display name in the configured workspace.

    Returns:
        dict: Cluster state and selected non-sensitive configuration fields.

    Raises:
        AidpError: The cluster is absent, ambiguous, or has an invalid name.
    """
    if not isinstance(cluster_name, str) or not cluster_name.strip():
        raise AidpError("Cluster name must be a nonempty string.")
    with workspace_clients(settings) as clients:
        instance_id, workspace_key, clusters, _, _ = clients
        cluster = find_cluster_status(
            clusters, instance_id, workspace_key, cluster_name
        )
        return _cluster_response(cluster)


def set_cluster_state(
    settings,
    cluster_name,
    action,
    *,
    wait=False,
    timeout_seconds=1200,
    confirm_action=False,
):
    """Start or stop one exact cluster in the configured workspace.

    Args:
        cluster_name: Exact cluster display name in the configured workspace.
        action: Requested lifecycle action, either ``start`` or ``stop``.
        wait: Poll until the requested state is observed.
        timeout_seconds: Positive maximum polling duration in seconds.
        confirm_action: Required explicit authorization for the mutation.

    Returns:
        dict: Sanitized cluster summary and lifecycle submission outcome.

    Raises:
        AidpError: Validation, state transition, submission, or polling fails.
    """
    require_confirmation(
        confirm_action,
        "Set confirm_action=true to submit a cluster lifecycle action.",
    )
    if not isinstance(cluster_name, str) or not cluster_name.strip():
        raise AidpError("Cluster name must be a nonempty string.")
    if action not in ("start", "stop"):
        raise AidpError("Cluster action must be either 'start' or 'stop'.")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or timeout_seconds < 1
    ):
        raise AidpError("timeout_seconds must be a positive integer.")
    with workspace_clients(settings) as clients:
        instance_id, workspace_key, clusters, _, _ = clients
        response = find_cluster_details(
            clusters, instance_id, workspace_key, cluster_name
        )
        cluster = response.data
        state = getattr(cluster, "state", None)
        desired, origin, transition = _cluster_transition_states(action)
        if state == desired:
            return _cluster_lifecycle_response(action, "already_desired", cluster)
        if state not in (origin, transition):
            raise AidpError(
                f"Cannot {action} a cluster in state {state!r}; inspect its status."
            )
        if state == origin:
            _submit_cluster_action(
                clusters,
                instance_id=instance_id,
                workspace_key=workspace_key,
                cluster=cluster,
                action=action,
                headers=response.headers,
            )
            outcome = "accepted"
        else:
            outcome = "already_transitioning"
        if not wait:
            return _cluster_lifecycle_response(action, outcome, cluster)
        return _wait_for_cluster_state(
            clusters,
            instance_id=instance_id,
            workspace_key=workspace_key,
            cluster_key=resource_key(cluster, "Cluster"),
            action=action,
            desired=desired,
            origin=origin,
            transition=transition,
            timeout_seconds=timeout_seconds,
        )


def _wait_for_cluster_state(
    clusters,
    *,
    instance_id,
    workspace_key,
    cluster_key,
    action,
    desired,
    origin,
    transition,
    timeout_seconds,
):
    """Poll a submitted lifecycle action without cancelling it on timeout."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        cluster = clusters.get_cluster(instance_id, workspace_key, cluster_key).data
        state = getattr(cluster, "state", None)
        if state == desired:
            return _cluster_lifecycle_response(action, "completed", cluster)
        if state not in (origin, transition):
            raise AidpError(
                "Cluster reached an unexpected state while waiting; inspect its "
                "status."
            )
        time.sleep(min(10, max(0.1, deadline - time.monotonic())))
    cluster = clusters.get_cluster(instance_id, workspace_key, cluster_key).data
    result = _cluster_lifecycle_response(action, "timed_out", cluster)
    result["timed_out"] = True
    return result


def _cluster_transition_states(action):
    """Return the desired, origin, and transition states for one action.

    Args:
        action: Previously validated lifecycle action.

    Returns:
        tuple[str, str, str]: Desired state, stable origin, and transition state.
    """
    return {
        "start": ("ACTIVE", "STOPPED", "STARTING"),
        "stop": ("STOPPED", "ACTIVE", "STOPPING"),
    }[action]


def _cluster_response(cluster):
    """Return an intentionally small, non-sensitive cluster summary."""
    return {
        "cluster_key": resource_key(cluster, "Cluster"),
        "display_name": getattr(cluster, "display_name", None),
        "type": getattr(cluster, "type", None),
        "state": getattr(cluster, "state", None),
        "state_details": getattr(cluster, "state_details", None),
        "runtime_version": getattr(
            getattr(cluster, "cluster_runtime_config", None), "runtime_version", None
        ),
        "node_type": getattr(cluster, "node_type", None),
        "driver": _shape_response(getattr(cluster, "driver_config", None)),
        "workers": _worker_response(getattr(cluster, "worker_config", None)),
        "auto_termination_minutes": getattr(cluster, "auto_termination_minutes", None),
    }


def _submit_cluster_action(
    clusters, *, instance_id, workspace_key, cluster, action, headers
):
    """Submit one lifecycle request without application-level retries.

    A transport error has an unknown remote outcome, so callers must inspect
    status rather than automatically submitting a second request.
    """
    options = {
        "retry_strategy": oci.retry.NoneRetryStrategy(),
        "opc_retry_token": str(uuid4()),
    }
    etag = headers.get("etag") if headers else None
    if etag:
        options["if_match"] = etag
    try:
        if action == "start":
            response = clusters.start_cluster(
                instance_id,
                workspace_key,
                resource_key(cluster, "Cluster"),
                models.StartClusterDetails(),
                **options,
            )
        else:
            response = clusters.stop_cluster(
                instance_id,
                workspace_key,
                resource_key(cluster, "Cluster"),
                models.StopClusterDetails(),
                **options,
            )
    except oci.exceptions.RequestException as exc:
        raise AidpError(
            "Cluster action outcome is unknown; check status before retrying."
        ) from exc
    if response.status != 202:
        raise AidpError(
            "Unexpected cluster action response; check status before retrying."
        )


def _cluster_lifecycle_response(action, outcome, cluster):
    """Return a lifecycle result without response bodies or trace metadata."""
    return {
        "action": action,
        "outcome": outcome,
        "cluster": _cluster_response(cluster),
    }


def _shape_response(configuration):
    """Return selected driver shape fields without serializing SDK objects."""
    if configuration is None:
        return None
    shape_config = getattr(configuration, "driver_shape_config", None)
    return {
        "node_type": getattr(configuration, "driver_node_type", None),
        "shape": getattr(configuration, "driver_shape", None),
        "ocpus": getattr(shape_config, "ocpus", None),
        "memory_in_gbs": getattr(shape_config, "memory_in_gbs", None),
    }


def _worker_response(configuration):
    """Return selected worker-count and shape fields without SDK internals."""
    if configuration is None:
        return None
    shape_config = getattr(configuration, "worker_shape_config", None)
    return {
        "shape": getattr(configuration, "worker_shape", None),
        "ocpus": getattr(shape_config, "ocpus", None),
        "memory_in_gbs": getattr(shape_config, "memory_in_gbs", None),
        "min_worker_count": getattr(configuration, "min_worker_count", None),
        "max_worker_count": getattr(configuration, "max_worker_count", None),
    }
