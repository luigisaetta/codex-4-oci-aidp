"""
Author: L. Saetta
Date last modified: 2026-09-15
License: MIT
Description: Offline SDK clients and HTTP fixtures; no OCI credentials or network.
"""

from types import SimpleNamespace

import pytest
from aidp_python_client.aidataplatform_dp import ClusterClient, WorkspaceClient


@pytest.fixture(name="sdk_clients")
def fixture_sdk_clients(sdk_factory):
    """Provide generated cluster clients through the common managed setup."""
    clusters, cluster_http = sdk_factory(ClusterClient)
    workspaces, workspace_http = sdk_factory(WorkspaceClient)
    return SimpleNamespace(
        clusters=clusters,
        workspaces=workspaces,
        cluster_http=cluster_http,
        workspace_http=workspace_http,
    )
