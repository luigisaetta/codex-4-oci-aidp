"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Offline tests for AI DP MCP catalog-volume operations.
"""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aidp_common.connection import AidpError
from aidp_mcp import volumes


def test_list_catalog_volumes_returns_only_external_metadata(monkeypatch):
    """External-volume discovery resolves the hierarchy and omits managed data."""
    settings = SimpleNamespace()
    catalogs, schemas, volume_client = Mock(), Mock(), Mock()
    catalog = SimpleNamespace(key="catalog-key", display_name="catalog")
    schema_a = SimpleNamespace(key="catalog.a", display_name="a")
    schema_b = SimpleNamespace(key="catalog.b", display_name="b")
    managed = SimpleNamespace(key="managed-key", display_name="managed")
    external_a = SimpleNamespace(key="external-a-key", display_name="external-a")
    external_b = SimpleNamespace(key="external-b-key", display_name="external-b")

    @contextmanager
    def catalog_clients():
        yield "instance", catalogs, schemas, volume_client

    monkeypatch.setattr(volumes, "catalog_clients", lambda _settings: catalog_clients())
    monkeypatch.setattr(
        volumes.oci.pagination,
        "list_call_get_all_results",
        Mock(
            side_effect=[
                SimpleNamespace(data=[catalog]),
                SimpleNamespace(data=[schema_b, schema_a]),
                SimpleNamespace(data=[managed, external_a]),
                SimpleNamespace(data=[external_b]),
            ]
        ),
    )
    volume_client.get_volume.side_effect = [
        SimpleNamespace(
            data=SimpleNamespace(
                key="managed-key",
                display_name="managed",
                volume_type="MANAGED",
                full_name="catalog.a.managed",
                lifecycle_state="ACTIVE",
            )
        ),
        SimpleNamespace(
            data=SimpleNamespace(
                key="external-a-key",
                display_name="external-a",
                volume_type="EXTERNAL",
                full_name="catalog.a.external-a",
                storage_location="oci://bucket@namespace/a/",
                lifecycle_state="ACTIVE",
            )
        ),
        SimpleNamespace(
            data=SimpleNamespace(
                key="external-b-key",
                display_name="external-b",
                volume_type="EXTERNAL",
                full_name="catalog.b.external-b",
                storage_location="oci://bucket@namespace/b/",
                lifecycle_state="ACTIVE",
            )
        ),
    ]

    result = volumes.list_catalog_volumes(settings, "catalog")

    assert result == {
        "catalog_name": "catalog",
        "external_only": True,
        "schemas": [
            {
                "display_name": "a",
                "volumes": [
                    {
                        "display_name": "external-a",
                        "volume_key": "external-a-key",
                        "full_name": "catalog.a.external-a",
                        "volume_type": "EXTERNAL",
                        "storage_location": "oci://bucket@namespace/a/",
                        "lifecycle_state": "ACTIVE",
                    }
                ],
            },
            {
                "display_name": "b",
                "volumes": [
                    {
                        "display_name": "external-b",
                        "volume_key": "external-b-key",
                        "full_name": "catalog.b.external-b",
                        "volume_type": "EXTERNAL",
                        "storage_location": "oci://bucket@namespace/b/",
                        "lifecycle_state": "ACTIVE",
                    }
                ],
            },
        ],
        "is_truncated": False,
    }


def test_list_volume_files_builds_a_sanitized_recursive_tree(monkeypatch):
    """File browsing returns hierarchy metadata without arbitrary SDK fields."""
    settings = SimpleNamespace()
    catalogs, schemas, volume_client = Mock(), Mock(), Mock()
    catalog = SimpleNamespace(key="catalog-key", display_name="catalog")
    schema = SimpleNamespace(key="catalog.schema", display_name="schema")
    volume = SimpleNamespace(key="volume-key", display_name="volume")

    @contextmanager
    def catalog_clients():
        yield "instance", catalogs, schemas, volume_client

    monkeypatch.setattr(volumes, "catalog_clients", lambda _settings: catalog_clients())
    monkeypatch.setattr(
        volumes.oci.pagination,
        "list_call_get_all_results",
        Mock(
            side_effect=[
                SimpleNamespace(data=[catalog]),
                SimpleNamespace(data=[schema]),
                SimpleNamespace(data=[volume]),
            ]
        ),
    )
    volume_client.list_files.return_value = SimpleNamespace(
        data=SimpleNamespace(
            items=[
                SimpleNamespace(
                    display_name="reports",
                    path="/Volumes/catalog/schema/volume/reports",
                    type="FOLDER",
                    time_created="folder-created",
                    time_updated="folder-updated",
                    metadata={"sensitive": "metadata"},
                ),
                SimpleNamespace(
                    display_name="summary.csv",
                    path="/Volumes/catalog/schema/volume/reports/2026/summary.csv",
                    type="FILE",
                    time_created="file-created",
                    time_updated="file-updated",
                    description="sensitive description",
                ),
            ]
        ),
        headers={},
    )

    result = volumes.list_volume_files(settings, "catalog", "schema", "volume")

    assert result["volume"] == {
        "catalog_name": "catalog",
        "schema_name": "schema",
        "display_name": "volume",
        "volume_key": "volume-key",
    }
    reports = result["root"]["children"][0]
    assert reports["display_name"] == "reports"
    assert reports["type"] == "FOLDER"
    inferred = reports["children"][0]
    assert inferred["inferred"] is True
    assert inferred["children"][0] == {
        "display_name": "summary.csv",
        "path": "/reports/2026/summary.csv",
        "type": "FILE",
        "time_created": "file-created",
        "time_updated": "file-updated",
    }
    assert result["is_truncated"] is False
    arguments = volume_client.list_files.call_args
    assert arguments.args == (
        "instance",
        "volume-key",
        "/",
    )
    assert arguments.kwargs["is_recursive"] is True


def test_list_volume_files_keeps_a_logical_child_path_in_the_request(monkeypatch):
    """The SDK receives the public path while mount-prefixed responses normalize."""
    settings = SimpleNamespace()
    catalogs, schemas, volume_client = Mock(), Mock(), Mock()
    catalog = SimpleNamespace(key="catalog-key", display_name="catalog")
    schema = SimpleNamespace(key="catalog.schema", display_name="schema")
    volume = SimpleNamespace(key="volume-key", display_name="volume")

    @contextmanager
    def catalog_clients():
        yield "instance", catalogs, schemas, volume_client

    monkeypatch.setattr(volumes, "catalog_clients", lambda _settings: catalog_clients())
    monkeypatch.setattr(
        volumes.oci.pagination,
        "list_call_get_all_results",
        Mock(
            side_effect=[
                SimpleNamespace(data=[catalog]),
                SimpleNamespace(data=[schema]),
                SimpleNamespace(data=[volume]),
            ]
        ),
    )
    volume_client.list_files.return_value = SimpleNamespace(
        data=SimpleNamespace(
            items=[
                SimpleNamespace(
                    display_name="train.jsonl",
                    path="/Volumes/catalog/schema/volume/datasets/train.jsonl",
                    type="FILE",
                    time_created="created",
                    time_updated="updated",
                )
            ]
        ),
        headers={},
    )

    result = volumes.list_volume_files(
        settings, "catalog", "schema", "volume", path="/datasets"
    )

    assert volume_client.list_files.call_args.args == (
        "instance",
        "volume-key",
        "/datasets",
    )
    assert result["root"] == {
        "display_name": "datasets",
        "path": "/datasets",
        "type": "FOLDER",
        "children": [
            {
                "display_name": "train.jsonl",
                "path": "/datasets/train.jsonl",
                "type": "FILE",
                "time_created": "created",
                "time_updated": "updated",
            }
        ],
    }


def test_list_volume_files_inspects_folders_when_recursive_listing_is_shallow(
    monkeypatch,
):
    """Direct-child AI DP responses are expanded into the promised tree."""
    settings = SimpleNamespace()
    catalogs, schemas, volume_client = Mock(), Mock(), Mock()
    catalog = SimpleNamespace(key="catalog-key", display_name="catalog")
    schema = SimpleNamespace(key="catalog.schema", display_name="schema")
    volume = SimpleNamespace(key="volume-key", display_name="volume")

    @contextmanager
    def catalog_clients():
        yield "instance", catalogs, schemas, volume_client

    monkeypatch.setattr(volumes, "catalog_clients", lambda _settings: catalog_clients())
    monkeypatch.setattr(
        volumes.oci.pagination,
        "list_call_get_all_results",
        Mock(
            side_effect=[
                SimpleNamespace(data=[catalog]),
                SimpleNamespace(data=[schema]),
                SimpleNamespace(data=[volume]),
            ]
        ),
    )
    volume_client.list_files.side_effect = [
        SimpleNamespace(
            data=SimpleNamespace(
                items=[
                    SimpleNamespace(
                        display_name="datasets",
                        path="/datasets",
                        type="FOLDER",
                        time_created="created",
                        time_updated="updated",
                    )
                ]
            ),
            headers={},
        ),
        SimpleNamespace(
            data=SimpleNamespace(
                items=[
                    SimpleNamespace(
                        display_name="train.jsonl",
                        path="/datasets/train.jsonl",
                        type="FILE",
                        time_created="created",
                        time_updated="updated",
                    )
                ]
            ),
            headers={},
        ),
    ]

    result = volumes.list_volume_files(settings, "catalog", "schema", "volume")

    assert [call.args[2] for call in volume_client.list_files.call_args_list] == [
        "/",
        "/datasets",
    ]
    assert result["root"]["children"][0]["children"][0]["path"] == (
        "/datasets/train.jsonl"
    )
    assert result["is_truncated"] is False


def test_list_volume_files_accepts_an_already_relative_response_path(monkeypatch):
    """AI DP responses without a mount prefix remain valid volume paths."""
    settings = SimpleNamespace()
    catalogs, schemas, volume_client = Mock(), Mock(), Mock()
    catalog = SimpleNamespace(key="catalog-key", display_name="catalog")
    schema = SimpleNamespace(key="catalog.schema", display_name="schema")
    volume = SimpleNamespace(key="volume-key", display_name="volume")

    @contextmanager
    def catalog_clients():
        yield "instance", catalogs, schemas, volume_client

    monkeypatch.setattr(volumes, "catalog_clients", lambda _settings: catalog_clients())
    monkeypatch.setattr(
        volumes.oci.pagination,
        "list_call_get_all_results",
        Mock(
            side_effect=[
                SimpleNamespace(data=[catalog]),
                SimpleNamespace(data=[schema]),
                SimpleNamespace(data=[volume]),
            ]
        ),
    )
    volume_client.list_files.return_value = SimpleNamespace(
        data=SimpleNamespace(
            items=[
                SimpleNamespace(
                    display_name="train.jsonl",
                    path="/datasets/train.jsonl",
                    type="FILE",
                    time_created="created",
                    time_updated="updated",
                )
            ]
        ),
        headers={},
    )

    result = volumes.list_volume_files(
        settings, "catalog", "schema", "volume", path="/datasets"
    )

    assert result["root"]["children"][0]["path"] == "/datasets/train.jsonl"


@pytest.mark.parametrize("value", [0, 1001, True, "100"])
def test_volume_tools_reject_unsafe_result_limits(value):
    """Volume tool bounds are enforced before remote discovery."""
    settings = SimpleNamespace()

    with pytest.raises(AidpError, match="max_results"):
        volumes.list_catalog_volumes(settings, "catalog", max_results=value)
    with pytest.raises(AidpError, match="max_results"):
        volumes.list_volume_files(
            settings, "catalog", "schema", "volume", max_results=value
        )
