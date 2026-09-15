"""
Author: L. Saetta
Date last modified: 2026-09-15
License: MIT
Description: Offline SDK clients and HTTP fixtures; no OCI credentials or network.
"""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import oci
import pytest
from oci._vendor import requests
from aidp_python_client.aidataplatform_dp import ClusterClient, WorkspaceClient


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """Reject unexpected network requests from any OCI SDK client."""
    monkeypatch.setattr(
        requests.sessions.Session,
        "request",
        Mock(side_effect=AssertionError("Live HTTP is forbidden in offline tests")),
    )


@pytest.fixture(name="http_response")
def fixture_http_response():
    """Build raw HTTP responses so the SDK performs real deserialization."""

    def build(data, status=200, **headers):
        if "state" in data:
            data = {
                "sourceApi": "CLUSTER_API",
                "key": "cluster",
                "displayName": "cluster",
                **data,
            }
        response = requests.Response()
        response.status_code = status
        body = json.dumps(data).encode()
        response._content = body  # pylint: disable=protected-access
        response.headers.update({"content-type": "application/json", **headers})
        return response

    return build


@pytest.fixture(name="sdk_clients")
def fixture_sdk_clients():
    """Instantiate Oracle clients with a fake signer and replace only HTTP I/O."""
    signer = Mock(spec=oci.auth.signers.SecurityTokenSigner)
    options = {
        "signer": signer,
        "retry_strategy": oci.retry.NoneRetryStrategy(),
        "timeout": (10, 30),
    }
    config = {"region": "eu-frankfurt-1"}
    clusters = ClusterClient(config, **options)
    workspaces = WorkspaceClient(config, **options)
    cluster_http = Mock()
    workspace_http = Mock()
    clusters.base_client.session.request = cluster_http
    workspaces.base_client.session.request = workspace_http
    yield SimpleNamespace(
        clusters=clusters,
        workspaces=workspaces,
        cluster_http=cluster_http,
        workspace_http=workspace_http,
    )
    clusters.base_client.session.close()
    workspaces.base_client.session.close()
