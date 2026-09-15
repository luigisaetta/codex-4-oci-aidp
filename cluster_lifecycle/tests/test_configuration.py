"""
Author: L. Saetta
Date last modified: 2026-09-15
License: MIT
Description: Pytest coverage for dotenv settings, endpoint derivation and SDK discovery.
"""

from pathlib import Path
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
    assert args.endpoint == "https://datalake.eu-frankfurt-1.oci.oraclecloud.com"


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
    assert args.endpoint == "https://datalake.us-ashburn-1.oci.oraclecloud.com"
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


def test_sdk_pagination_and_workspace_name_filter():
    """Exercise real OCI pagination over collection models with fake service calls."""
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
    workbench = Mock()
    workbench.items.side_effect = [
        [{"key": "ignore", "displayName": "other"}],
        [{"key": "chosen", "displayName": "workspace"}],
        [{"key": "cluster-key", "displayName": "cluster", "type": "USER"}],
    ]
    target = lifecycle.discover(
        control,
        workbench,
        "compartment",
        "cluster",
        workspace_name="workspace",
        cluster_type="USER",
    )
    assert target == lifecycle.Target("second", "chosen", "cluster-key")
    assert control.list_ai_data_platforms.call_args.kwargs["page"] == "next"
    assert workbench.items.call_count == 3


def test_discovery_failure_cannot_mutate():
    """Do not ignore an inaccessible workspace during uniqueness discovery."""
    control, workbench = Mock(), Mock()
    control.get_ai_data_platform.return_value.data = SimpleNamespace(
        id="instance", compartment_id="compartment", lifecycle_state="ACTIVE"
    )
    workbench.items.side_effect = lifecycle.LifecycleError("Access denied")
    with pytest.raises(lifecycle.LifecycleError, match="Access denied"):
        lifecycle.discover(
            control, workbench, "compartment", "cluster", instance_id="instance"
        )
    workbench.request.assert_not_called()


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
    change = Mock()
    monkeypatch.setattr(lifecycle, "change_state", change)
    assert lifecycle.main(["status", "--env-file", str(env_file)]) == 0
    assert from_file.call_args.args[1] == "TEST"
    assert control.call_args.args[0]["region"] == "eu-frankfurt-1"
    assert control.call_args.kwargs["signer"] is signer.return_value
    assert discover.call_args.kwargs["workspace_name"] == "workspace"
    assert change.call_args.args[2] == "status"
