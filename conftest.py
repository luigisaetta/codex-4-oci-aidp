"""
Author: L. Saetta
Date last modified: 2026-09-15
License: MIT
Description: Offline SDK clients and HTTP fixtures; no OCI credentials or network.
"""

import json
from contextlib import ExitStack
from unittest.mock import Mock

import oci
import pytest
from oci._vendor import requests

from aidp_common.connection import managed_client


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


@pytest.fixture(name="sdk_factory")
def fixture_sdk_factory():
    """Create any real Workbench client with managed cleanup and fake HTTP."""
    signer = Mock(spec=oci.auth.signers.SecurityTokenSigner)
    with ExitStack() as resources:

        def create(client_type):
            client = managed_client(
                resources,
                client_type,
                {"region": "eu-frankfurt-1"},
                {
                    "signer": signer,
                    "timeout": (10, 30),
                    "retry_strategy": oci.retry.NoneRetryStrategy(),
                },
                preserve_timestamps=True,
            )
            transport = Mock()
            client.base_client.session.request = transport
            return client, transport

        yield create
