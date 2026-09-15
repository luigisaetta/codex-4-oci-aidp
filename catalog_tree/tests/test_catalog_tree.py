"""
Author: L. Saetta
Date last modified: 2026-09-15
License: MIT
Description: Offline catalog discovery, SDK pagination, rendering and CLI tests.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import oci
import pytest
from aidp_python_client.aidataplatform_dp import (
    CatalogClient,
    SchemaClient,
    VolumeClient,
    models,
)

import catalog_tree as feature
from aidp_common import settings


@pytest.fixture(name="catalog_clients")
def fixture_catalog_clients(sdk_factory):
    """Use real Oracle catalog clients and replaced HTTP transport."""
    catalogs, catalog_http = sdk_factory(CatalogClient)
    schemas, schema_http = sdk_factory(SchemaClient)
    volumes, volume_http = sdk_factory(VolumeClient)
    return SimpleNamespace(
        catalogs=catalogs,
        schemas=schemas,
        volumes=volumes,
        catalog_http=catalog_http,
        schema_http=schema_http,
        volume_http=volume_http,
    )


@pytest.fixture(name="env_file")
def fixture_env_file(tmp_path, monkeypatch):
    """Isolate shared settings while retaining irrelevant lifecycle variables."""
    monkeypatch.setattr(settings.os, "environ", {})
    path = tmp_path / ".env"
    path.write_text(
        "COMPARTMENT=demo\nREGION=eu-frankfurt-1\n"
        "WORKSPACE_NAME=ignored\nWORKSPACE_KEY=also-ignored\n"
        "ACTION=stop\nWAIT=invalid\n",
        encoding="utf-8",
    )
    return path


def _control():
    control = Mock()
    control.get_ai_data_platform.return_value.data = SimpleNamespace(
        id="instance", compartment_id="compartment", lifecycle_state="ACTIVE"
    )
    return control


def test_all_pages_qualified_keys_and_sorted_tree(catalog_clients, http_response):
    """The full hierarchy uses server keys and all pages, including empty ones."""
    clients = catalog_clients
    dates = {"timeCreated": 1750000000000}
    clients.catalog_http.side_effect = [
        http_response({"items": []}, **{"opc-next-page": "catalog-next"}),
        http_response(
            {"items": [{"key": "catalog-key", "displayName": "fine_tuning", **dates}]}
        ),
    ]
    instance, catalog = feature.find_catalog(
        _control(), clients.catalogs, "compartment", "fine_tuning", "instance"
    )
    clients.schema_http.side_effect = [
        http_response(
            {
                "items": [
                    {
                        "key": "fine_tuning.z",
                        "displayName": "z",
                        "entityType": "ALH",
                        **dates,
                    }
                ]
            },
            **{"opc-next-page": "schema-next"},
        ),
        http_response(
            {
                "items": [
                    {
                        "key": "fine_tuning.a",
                        "displayName": "a",
                        "entityType": "ALH",
                        **dates,
                    }
                ]
            }
        ),
    ]
    clients.volume_http.side_effect = [
        http_response(
            {"items": [{"key": "v2", "displayName": "volume2", **dates}]},
            **{"opc-next-page": "volume-next"},
        ),
        http_response({"items": [{"key": "v1", "displayName": "volume1", **dates}]}),
        http_response({"items": []}),
    ]
    tree = feature.read_tree(clients.schemas, clients.volumes, instance, catalog)
    assert feature.render_tree(tree) == (
        "fine_tuning\n├── a\n│   ├── volume1\n│   └── volume2\n└── z"
    )
    query = dict(clients.volume_http.call_args_list[0].kwargs["params"])
    assert query["schemaKey"] == "fine_tuning.a"
    assert query["catalogKey"] == "catalog-key"
    assert (
        dict(clients.volume_http.call_args_list[1].kwargs["params"])["page"]
        == "volume-next"
    )
    assert dict(clients.schema_http.call_args.kwargs["params"])["page"] == "schema-next"
    assert (
        dict(clients.catalog_http.call_args.kwargs["params"])["page"] == "catalog-next"
    )
    for transport in (clients.catalog_http, clients.schema_http, clients.volume_http):
        assert all(call.args[0] == "GET" for call in transport.call_args_list)


@pytest.mark.parametrize("count", [0, 2])
def test_catalog_missing_or_ambiguous(catalog_clients, http_response, count):
    """Do not choose an absent or ambiguous catalog, even across pages."""
    catalog_clients.catalog_http.side_effect = [
        http_response(
            {"items": [{"key": str(i), "displayName": "wanted"}]},
            **({"opc-next-page": str(i + 1)} if i < count - 1 else {}),
        )
        for i in range(count)
    ] or [http_response({"items": [{"key": "other", "displayName": "Wanted"}]})]
    with pytest.raises(feature.AidpError, match=f"{count} visible matches"):
        feature.find_catalog(
            _control(), catalog_clients.catalogs, "compartment", "wanted", "instance"
        )
    catalog_clients.schema_http.assert_not_called()


def test_empty_catalog(catalog_clients, http_response):
    """An empty catalog prints its own name and performs no volume lookup."""
    catalog_clients.schema_http.return_value = http_response({"items": []})
    tree = feature.read_tree(
        catalog_clients.schemas,
        catalog_clients.volumes,
        "instance",
        models.CatalogSummary(key="key", display_name="empty"),
    )
    assert feature.render_tree(tree) == "empty"
    catalog_clients.volume_http.assert_not_called()


def test_renderer_last_branch_and_control_characters():
    """Branch drawing and escaping keep each label on one safe terminal line."""
    tree = feature.CatalogTree("c\n", (feature.SchemaNode("s\x1b", ("v\t", "é")),))
    assert feature.render_tree(tree) == "c\\n\n└── s\\x1b\n    ├── v\\t\n    └── é"


def test_settings_ignore_cluster_values(env_file, monkeypatch):
    """Catalog commands only validate their own arguments and shared connection."""
    args = feature.parse_settings(["fine_tuning", "--env-file", str(env_file)])
    assert args.catalog_name == "fine_tuning"
    assert args.compartment == "demo"
    assert args.region == "eu-frankfurt-1"
    assert not hasattr(args, "workspace_key")
    assert not hasattr(args, "action")
    monkeypatch.setitem(settings.os.environ, "COMPARTMENT", "environment")
    args = feature.parse_settings(
        ["fine_tuning", "--env-file", str(env_file), "--compartment", "cli"]
    )
    assert args.compartment == "cli"
    with pytest.raises(SystemExit):
        feature.parse_settings(["--env-file", str(env_file)])


def test_cli_does_not_print_partial_tree(
    catalog_clients, http_response, env_file, monkeypatch, capsys
):
    """A late volume permission error leaves stdout empty and sessions closed."""
    clients = catalog_clients
    monkeypatch.setattr(feature, "load_auth", Mock(return_value=({"tenancy": "t"}, {})))
    identity = Mock()
    control = _control()
    constructors = {
        oci.identity.IdentityClient: identity,
        oci.ai_data_platform.AiDataPlatformClient: control,
        CatalogClient: clients.catalogs,
        SchemaClient: clients.schemas,
        VolumeClient: clients.volumes,
    }
    closed = []

    def create(resources, client_type, *_args, **_kwargs):
        resources.callback(closed.append, client_type)
        return constructors[client_type]

    monkeypatch.setattr(feature, "managed_client", create)
    monkeypatch.setattr(
        feature, "resolve_compartment", Mock(return_value="compartment")
    )
    monkeypatch.setattr(
        feature,
        "find_catalog",
        Mock(
            return_value=(
                "instance",
                models.CatalogSummary(key="c", display_name="catalog"),
            )
        ),
    )
    clients.schema_http.return_value = http_response(
        {
            "items": [
                {"key": "c.a", "displayName": "a", "entityType": "ALH"},
                {"key": "c.b", "displayName": "b", "entityType": "ALH"},
            ]
        }
    )
    clients.volume_http.side_effect = [
        http_response({"items": []}),
        http_response({"code": "Forbidden", "message": "SECRET"}, 403),
    ]
    assert feature.main(["catalog", "--env-file", str(env_file)]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "HTTP 403" in output.err
    assert "| START" in output.err and "| END" in output.err
    assert "SECRET" not in output.err
    assert len(closed) == 5


def test_cli_success_keeps_tree_on_stdout(env_file, monkeypatch, capsys):
    """Banners do not interfere with redirecting the tree to a text file."""

    def execute(_args):
        print(feature.render_tree(feature.CatalogTree("catalog", ())))
        return 0

    monkeypatch.setattr(feature, "_execute", execute)
    assert feature.main(["catalog", "--env-file", str(env_file)]) == 0
    output = capsys.readouterr()
    assert output.out == "catalog\n"
    assert "Elapsed time" in output.err
