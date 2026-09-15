"""
Author: L. Saetta
Date last modified: 2026-09-15
License: MIT
Description: Pytest coverage for dotenv settings, endpoint derivation and SDK discovery.
"""

from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import oci
import pytest

import configuration
import cluster_lifecycle as lifecycle


@pytest.fixture(name="env_file")
def fixture_env_file(tmp_path, monkeypatch):
    """Isolate settings from the user's dotenv and process environment."""
    monkeypatch.setattr(configuration.os, "environ", {})
    path = tmp_path / ".env"
    path.write_text("COMPARTMENT=demo\nCLUSTER_NAME=cluster\n", encoding="utf-8")
    return path


def test_frankfurt_defaults_and_dotenv_location(env_file, monkeypatch, tmp_path):
    """The default dotenv path works independently of the current directory."""
    repository_root = Path(__file__).resolve().parents[2]
    assert configuration.DEFAULT_ENV_FILE == repository_root / ".env"
    monkeypatch.setattr(configuration, "DEFAULT_ENV_FILE", env_file)
    monkeypatch.chdir(tmp_path.parent)
    args = configuration.parse_settings([])
    assert args.compartment == "demo"
    assert args.action == "status"
    assert args.region == "eu-frankfurt-1"
    assert args.endpoint is None  # The installed SDK resolves the regional endpoint.


def test_config_precedence_and_boolean_overrides(env_file, monkeypatch):
    """CLI overrides environment, which overrides dotenv, without mutation."""
    env_file.write_text(
        "COMPARTMENT=file\nCLUSTER_NAME=cluster\nREGION=eu-frankfurt-1\n"
        "WAIT=true\nDRY_RUN=true\nWAIT_TIMEOUT=42\nWORKSPACE_NAME=demo-workspace\n",
        encoding="utf-8",
    )
    process_env = {"COMPARTMENT": "environment"}
    monkeypatch.setattr(configuration.os, "environ", process_env)
    args = configuration.parse_settings(["--env-file", str(env_file)])
    assert args.compartment == "environment"
    assert args.wait and args.dry_run
    assert args.wait_timeout == 42
    assert args.workspace_name == "demo-workspace"
    args = configuration.parse_settings(
        [
            "stop",
            "--env-file",
            str(env_file),
            "--compartment",
            "cli",
            "--no-wait",
            "--no-dry-run",
            "--region",
            "us-ashburn-1",
        ]
    )
    assert args.compartment == "cli"
    assert args.action == "stop"
    assert not args.wait and not args.dry_run
    assert args.region == "us-ashburn-1"
    assert args.endpoint is None
    assert process_env == {"COMPARTMENT": "environment"}


@pytest.mark.parametrize(
    "extra",
    [
        "WAIT=maybe",
        "WAIT_TIMEOUT=0",
        "POLL_INTERVAL=abc",
        "REGION=../../bad",
        "WORKSPACE_KEY=key\nWORKSPACE_NAME=name",
        "CLUSTER_TYPE=bad",
        "ACTION=delete",
        "AIDP_ENDPOINT=http://example.invalid",
        "CLUSTER_NAME=",
    ],
)
def test_invalid_settings_fail_before_cloud_access(env_file, extra):
    """Invalid settings consistently produce CLI error code 2."""
    with env_file.open("a", encoding="utf-8") as stream:
        stream.write(extra + "\n")
    with pytest.raises(SystemExit) as error:
        configuration.parse_settings(["--env-file", str(env_file)])
    assert error.value.code == 2


def test_optional_blanks_and_endpoint_override(env_file):
    """Empty selectors remain unset and a trusted HTTPS override is accepted."""
    with env_file.open("a", encoding="utf-8") as stream:
        stream.write(
            "WORKSPACE_KEY=\nCLUSTER_TYPE=\nAIDP_ENDPOINT=https://example.invalid/\n"
        )
    args = configuration.parse_settings(["--env-file", str(env_file)])
    assert args.workspace_key is None
    assert args.cluster_type is None
    assert args.endpoint == "https://example.invalid"


def test_explicit_missing_env_file(env_file):
    """An explicitly selected missing file is an error, even with CLI targets."""
    env_file.unlink()
    with pytest.raises(SystemExit) as error:
        configuration.parse_settings(
            [
                "--env-file",
                str(env_file),
                "--compartment",
                "demo",
                "--cluster-name",
                "demo",
            ]
        )
    assert error.value.code == 2


def test_sdk_pagination_and_workspace_name_filter(sdk_clients, http_response):
    """Exercise real OCI and AI DP pagination with fake service responses."""
    control = Mock()
    control.list_ai_data_platforms.__name__ = "list_ai_data_platforms"
    model = oci.ai_data_platform.models
    control.list_ai_data_platforms.side_effect = [
        oci.response.Response(
            200,
            {"opc-next-page": "next"},
            model.AiDataPlatformCollection(
                items=[model.AiDataPlatformSummary(id="first")]
            ),
            None,
        ),
        oci.response.Response(
            200,
            {},
            model.AiDataPlatformCollection(
                items=[model.AiDataPlatformSummary(id="second")]
            ),
            None,
        ),
    ]
    sdk_clients.workspace_http.side_effect = [
        http_response({"items": [{"key": "ignore", "displayName": "other"}]}),
        http_response({"items": []}, **{"opc-next-page": "ws-page"}),
        http_response({"items": [{"key": "chosen", "displayName": "workspace"}]}),
    ]
    sdk_clients.cluster_http.side_effect = [
        http_response({"items": []}, **{"opc-next-page": "cluster-page"}),
        http_response(
            {
                "items": [
                    {"key": "cluster-key", "displayName": "cluster", "type": "USER"}
                ]
            }
        ),
    ]
    target = lifecycle.discover(
        control,
        sdk_clients.workspaces,
        sdk_clients.clusters,
        "compartment",
        "cluster",
        workspace_name="workspace",
        cluster_type="USER",
    )
    assert target == lifecycle.Target("second", "chosen", "cluster-key")
    assert control.list_ai_data_platforms.call_args.kwargs["page"] == "next"
    assert sdk_clients.workspace_http.call_count == 3
    query = dict(sdk_clients.cluster_http.call_args.kwargs["params"])
    assert query["page"] == "cluster-page"
    assert query["displayName"] == "cluster"
    assert query["type"] == "USER"


@pytest.mark.parametrize("count", [0, 1, 2])
def test_discovery_unique_match(sdk_clients, http_response, count):
    """Only a unique name match across all result pages can be selected."""
    control = Mock()
    control.get_ai_data_platform.return_value.data = SimpleNamespace(
        id="instance", compartment_id="compartment", lifecycle_state="ACTIVE"
    )
    sdk_clients.workspace_http.return_value = http_response(
        {"items": [{"key": "workspace"}]}
    )
    sdk_clients.cluster_http.side_effect = [
        http_response(
            {"items": [{"key": str(i), "displayName": "cluster"}]},
            **({"opc-next-page": str(i + 1)} if i < count - 1 else {}),
        )
        for i in range(count)
    ] or [http_response({"items": []})]
    if count == 1:
        target = lifecycle.discover(
            control,
            sdk_clients.workspaces,
            sdk_clients.clusters,
            "compartment",
            "cluster",
            instance_id="instance",
        )
        assert target.cluster_key == "0"
    else:
        with pytest.raises(lifecycle.LifecycleError, match="visible matches"):
            lifecycle.discover(
                control,
                sdk_clients.workspaces,
                sdk_clients.clusters,
                "compartment",
                "cluster",
                instance_id="instance",
            )
    assert all(c.args[0] == "GET" for c in sdk_clients.cluster_http.call_args_list)


def test_instance_compartment_boundary(sdk_clients):
    """An explicit instance must still belong to the selected compartment."""
    control = Mock()
    control.get_ai_data_platform.return_value.data = SimpleNamespace(
        compartment_id="other", lifecycle_state="ACTIVE"
    )
    with pytest.raises(lifecycle.LifecycleError, match="requested compartment"):
        lifecycle.discover(
            control,
            sdk_clients.workspaces,
            sdk_clients.clusters,
            "compartment",
            "cluster",
            instance_id="instance",
        )
    sdk_clients.workspace_http.assert_not_called()
    sdk_clients.cluster_http.assert_not_called()


def test_discovery_failure_cannot_mutate(sdk_clients, http_response):
    """Do not ignore an inaccessible workspace during uniqueness discovery."""
    control = Mock()
    control.get_ai_data_platform.return_value.data = SimpleNamespace(
        id="instance", compartment_id="compartment", lifecycle_state="ACTIVE"
    )
    sdk_clients.workspace_http.return_value = http_response(
        {"code": "NotAuthorized", "message": "denied"}, 403
    )
    with pytest.raises(oci.exceptions.ServiceError):
        lifecycle.discover(
            control,
            sdk_clients.workspaces,
            sdk_clients.clusters,
            "compartment",
            "cluster",
            instance_id="instance",
        )
    sdk_clients.cluster_http.assert_not_called()


def test_main_uses_selected_profile_region_and_workspace(env_file, monkeypatch):
    """Wire validated settings into SDK authentication and read-only execution."""
    with env_file.open("a", encoding="utf-8") as stream:
        stream.write("OCI_PROFILE=TEST\nWORKSPACE_NAME=workspace\n")
    config = {
        "tenancy": "tenancy",
        "user": "user",
        "fingerprint": "fingerprint",
        "key_file": "~/.oci/example.pem",
        "region": "us-ashburn-1",
    }
    from_file = Mock(return_value=config)
    monkeypatch.setattr(lifecycle.oci.config, "from_file", from_file)
    monkeypatch.setattr(lifecycle.oci.config, "validate_config", Mock())
    signer = Mock()
    monkeypatch.setattr(lifecycle.oci.signer, "Signer", signer)
    monkeypatch.setattr(lifecycle.oci.identity, "IdentityClient", Mock())
    control = Mock()
    monkeypatch.setattr(lifecycle.oci.ai_data_platform, "AiDataPlatformClient", control)
    monkeypatch.setattr(
        lifecycle, "resolve_compartment", Mock(return_value="compartment")
    )
    discover = Mock(return_value=lifecycle.Target("instance", "workspace", "cluster"))
    monkeypatch.setattr(lifecycle, "discover", discover)
    workspace_constructor = Mock()
    workspace_constructor.return_value.base_client.type_mappings = {}
    monkeypatch.setattr(lifecycle, "WorkspaceClient", workspace_constructor)
    clusters = Mock()
    clusters.return_value.base_client.type_mappings = {}
    monkeypatch.setattr(lifecycle, "ClusterClient", clusters)
    change = Mock()
    monkeypatch.setattr(lifecycle, "change_state", change)
    assert lifecycle.main(["status", "--env-file", str(env_file)]) == 0
    assert from_file.call_args.args[1] == "TEST"
    assert control.call_args.args[0]["region"] == "eu-frankfurt-1"
    assert control.call_args.kwargs["signer"] is signer.return_value
    assert discover.call_args.kwargs["workspace_name"] == "workspace"
    assert change.call_args.args[2] == "status"
    assert "service_endpoint" not in clusters.call_args.kwargs
    assert clusters.call_args.args[0]["region"] == "eu-frankfurt-1"
    clusters.return_value.base_client.session.close.assert_called_once()


@pytest.mark.parametrize("outcome", [0, 1, KeyboardInterrupt()])
def test_execution_banners_cover_success_failure_and_interrupt(
    env_file, monkeypatch, capsys, outcome
):
    """Every started execution prints final timing and preserves its exit status."""
    execute = Mock(return_value=outcome)
    if isinstance(outcome, KeyboardInterrupt):
        execute.side_effect = outcome
    monkeypatch.setattr(lifecycle, "_execute", execute)
    monkeypatch.setattr(lifecycle.time, "monotonic", Mock(side_effect=[10.0, 12.5]))
    clock = Mock()
    clock.now.side_effect = [
        datetime(2026, 9, 15, 10, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 15, 10, 0, 2, tzinfo=timezone.utc),
    ]
    monkeypatch.setattr(lifecycle, "datetime", clock)
    result = lifecycle.main(["start", "--dry-run", "--env-file", str(env_file)])
    assert result == (130 if isinstance(outcome, KeyboardInterrupt) else outcome)
    output = capsys.readouterr().out
    assert output.index("| START") < output.index("| END")
    assert output.count("### Operation    : START (DRY RUN)") == 2
    assert "2026-09-15T10:00:00+00:00" in output
    assert "2026-09-15T10:00:02+00:00" in output
    assert "### Elapsed time : 2.500 seconds" in output


@pytest.mark.parametrize(
    "error",
    [
        oci.exceptions.ServiceError(403, "Forbidden", {}, "SECRET"),
        oci.exceptions.RequestException("SECRET"),
    ],
)
def test_cli_errors_are_redacted(env_file, monkeypatch, capsys, error):
    """The CLI preserves failure diagnostics without exposing SDK exception text."""
    monkeypatch.setattr(lifecycle.oci.config, "from_file", Mock(side_effect=error))
    assert lifecycle.main(["status", "--env-file", str(env_file)]) == 1
    output = capsys.readouterr()
    assert "SECRET" not in output.err + output.out
    assert "| END" in output.out


def test_compartment_ocid_and_ambiguous_name(monkeypatch):
    """An OCID avoids IAM lookup; duplicate names are rejected."""
    identity = Mock()
    ocid = "ocid1.compartment.oc1..example"
    assert lifecycle.resolve_compartment(identity, "tenancy", ocid) == ocid
    identity.list_compartments.assert_not_called()
    result = SimpleNamespace(
        data=[
            SimpleNamespace(id="one", name="demo"),
            SimpleNamespace(id="two", name="demo"),
        ]
    )
    monkeypatch.setattr(
        oci.pagination, "list_call_get_all_results", Mock(return_value=result)
    )
    with pytest.raises(lifecycle.LifecycleError, match="2 visible matches"):
        lifecycle.resolve_compartment(identity, "tenancy", "demo")
