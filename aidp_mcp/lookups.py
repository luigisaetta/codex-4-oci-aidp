"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Exact-name AI DP resource lookup helpers.
"""

from dataclasses import dataclass

import oci

from aidp_common.connection import AidpError, validate_resource_key


@dataclass(frozen=True)
class JobTarget:
    """Resolved AI DP instance, workspace, and cluster identifiers."""

    instance_id: str
    workspace_key: str
    cluster_key: str
    cluster_name: str


def _resource_key(resource, label):
    value = getattr(resource, "key", None)
    if not isinstance(value, str) or not value:
        raise AidpError(f"{label} response is missing its key.")
    validate_resource_key(value)
    return value


def next_page(response):
    """Return the optional OCI pagination token from one SDK response.

    Args:
        response: OCI SDK response whose headers may contain a next-page token.

    Returns:
        str | None: The next-page token, if supplied by OCI.
    """
    return (getattr(response, "headers", None) or {}).get("opc-next-page")


def find_workspace(workspaces, instance_id, workspace_name):
    """Resolve one exact workspace name within an AI DP instance.

    Args:
        workspaces: Generated WorkspaceClient.
        instance_id: AI DP instance OCID.
        workspace_name: Exact workspace display name.

    Returns:
        str: Workspace key.

    Raises:
        AidpError: The workspace is absent or ambiguous.
    """
    matches = [
        item
        for item in oci.pagination.list_call_get_all_results(
            workspaces.list_workspaces, instance_id, display_name=workspace_name
        ).data
        if getattr(item, "display_name", None) == workspace_name
    ]
    if len(matches) != 1:
        raise AidpError(f"Workspace name has {len(matches)} visible matches.")
    return _resource_key(matches[0], "Workspace")


def find_cluster(clusters, instance_id, workspace_key, cluster_name):
    """Resolve one exact active cluster in a workspace.

    Args:
        clusters: Generated ClusterClient.
        instance_id: AI DP instance OCID.
        workspace_key: Workspace key.
        cluster_name: Exact cluster display name.

    Returns:
        JobTarget: Resolved cluster target.

    Raises:
        AidpError: The cluster is absent, ambiguous, or not active.
    """
    matches = [
        item
        for item in oci.pagination.list_call_get_all_results(
            clusters.list_clusters,
            instance_id,
            workspace_key,
            display_name=cluster_name,
        ).data
        if getattr(item, "display_name", None) == cluster_name
    ]
    if len(matches) != 1:
        raise AidpError(f"Cluster name has {len(matches)} visible matches.")
    key = _resource_key(matches[0], "Cluster")
    cluster = clusters.get_cluster(instance_id, workspace_key, key).data
    if getattr(cluster, "state", None) != "ACTIVE":
        raise AidpError("Selected cluster must be ACTIVE before a job run.")
    return JobTarget(instance_id, workspace_key, key, cluster_name)


def find_cluster_status(clusters, instance_id, workspace_key, cluster_name):
    """Resolve one exact cluster and return its current detailed SDK model.

    Args:
        clusters: Generated ClusterClient.
        instance_id: Selected AI DP instance OCID.
        workspace_key: Selected workspace key.
        cluster_name: Exact cluster display name.

    Returns:
        object: The detailed cluster model returned by AI DP.

    Raises:
        AidpError: The cluster is absent, ambiguous, or lacks a resource key.
    """
    return find_cluster_details(clusters, instance_id, workspace_key, cluster_name).data


def find_cluster_details(clusters, instance_id, workspace_key, cluster_name):
    """Resolve one exact cluster and return its detailed SDK response.

    This preserves the ETag required to protect a lifecycle mutation from a
    concurrent cluster update.

    Args:
        clusters: Generated ClusterClient.
        instance_id: Selected AI DP instance OCID.
        workspace_key: Selected workspace key.
        cluster_name: Exact cluster display name.

    Returns:
        oci.response.Response: Detailed cluster response, including headers.

    Raises:
        AidpError: The cluster is absent, ambiguous, or lacks a resource key.
    """
    matches = [
        item
        for item in oci.pagination.list_call_get_all_results(
            clusters.list_clusters,
            instance_id,
            workspace_key,
            display_name=cluster_name,
        ).data
        if getattr(item, "display_name", None) == cluster_name
    ]
    if len(matches) != 1:
        raise AidpError(f"Expected exactly one cluster named {cluster_name!r}.")
    key = _resource_key(matches[0], "Cluster")
    return clusters.get_cluster(instance_id, workspace_key, key)


def _sorted_named_resources(resources, label):
    """Validate and sort remote resource summaries deterministically."""
    checked = []
    for resource in resources:
        display_name = getattr(resource, "display_name", None)
        if not isinstance(display_name, str) or not display_name:
            raise AidpError(f"{label} response is missing its display name.")
        _resource_key(resource, label)
        checked.append(resource)
    return sorted(
        checked,
        key=lambda item: (
            item.display_name.casefold(),
            item.display_name,
            getattr(item, "key"),
        ),
    )
