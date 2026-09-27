"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Shared authentication and managed-client resource tests.
"""

from contextlib import ExitStack
from pathlib import Path
import re
import tomllib
from types import SimpleNamespace
from unittest.mock import Mock

import oci
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from aidp_common.connection import AidpError, load_auth, managed_client
from aidp_common.settings import connection_parser


def test_connection_parser_exposes_optional_workspace_name(tmp_path):
    """Workspace consumers can read the shared workspace setting."""
    env_file = tmp_path / ".env"
    env_file.write_text("WORKSPACE_NAME=example-workspace\n", encoding="utf-8")

    parser, _, _ = connection_parser([], "Test settings.", env_file)

    assert parser.parse_args([]).workspace_name == "example-workspace"


def test_environment_settings_file_overrides_repository_default(tmp_path, monkeypatch):
    """An absolute process setting selects its file instead of the default."""
    default_file = tmp_path / "repository.env"
    selected_file = tmp_path / "selected.env"
    default_file.write_text("WORKSPACE_NAME=repository\n", encoding="utf-8")
    selected_file.write_text("WORKSPACE_NAME=selected\n", encoding="utf-8")
    monkeypatch.setenv("AIDP_ENV_FILE", str(selected_file))

    parser, _, env_path = connection_parser([], "Test settings.", default_file)

    assert env_path == selected_file
    assert parser.parse_args([]).workspace_name == "selected"


def test_command_line_settings_file_overrides_environment(tmp_path, monkeypatch):
    """The command-line settings path retains its highest precedence."""
    environment_file = tmp_path / "environment.env"
    command_line_file = tmp_path / "command-line.env"
    environment_file.write_text("WORKSPACE_NAME=environment\n", encoding="utf-8")
    command_line_file.write_text("WORKSPACE_NAME=command-line\n", encoding="utf-8")
    monkeypatch.setenv("AIDP_ENV_FILE", str(environment_file))

    parser, _, env_path = connection_parser(
        ["--env-file", str(command_line_file)], "Test settings."
    )

    assert env_path == command_line_file
    assert parser.parse_args([]).workspace_name == "command-line"


def test_process_environment_overrides_selected_settings_file(tmp_path, monkeypatch):
    """Ordinary process variables retain precedence over dotenv values."""
    selected_file = tmp_path / "selected.env"
    selected_file.write_text("REGION=eu-frankfurt-1\n", encoding="utf-8")
    monkeypatch.setenv("AIDP_ENV_FILE", str(selected_file))
    monkeypatch.setenv("REGION", "us-ashburn-1")

    parser, _, _ = connection_parser([], "Test settings.")

    assert parser.parse_args([]).region == "us-ashburn-1"


def test_relative_environment_settings_file_is_rejected(monkeypatch):
    """A working-directory-dependent settings selector is never accepted."""
    monkeypatch.setenv("AIDP_ENV_FILE", "settings.env")

    with pytest.raises(SystemExit, match="2"):
        connection_parser([], "Test settings.")


def test_missing_environment_settings_file_hides_its_path(
    tmp_path, monkeypatch, capsys
):
    """A missing selected file reports its selector but not its local path."""
    missing = tmp_path / "missing.env"
    monkeypatch.setenv("AIDP_ENV_FILE", str(missing))

    with pytest.raises(SystemExit) as error:
        connection_parser([], "Test settings.")

    assert error.value.code == 2
    stderr = capsys.readouterr().err
    assert "AIDP_ENV_FILE" in stderr
    assert str(missing) not in stderr


def test_dotenv_cannot_select_another_settings_file(tmp_path, monkeypatch):
    """AIDP_ENV_FILE inside dotenv is data, not a recursive selector."""
    default_file = tmp_path / "default.env"
    ignored_file = tmp_path / "ignored.env"
    ignored_file.write_text("WORKSPACE_NAME=ignored\n", encoding="utf-8")
    default_file.write_text(
        f"AIDP_ENV_FILE={ignored_file}\nWORKSPACE_NAME=default\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("AIDP_ENV_FILE", raising=False)

    parser, _, env_path = connection_parser([], "Test settings.", default_file)

    assert env_path == default_file
    assert parser.parse_args([]).workspace_name == "default"


def test_missing_default_settings_file_remains_an_empty_source(tmp_path, monkeypatch):
    """The absent repository default keeps the established empty-dotenv behavior."""
    missing = tmp_path / "missing-default.env"
    monkeypatch.delenv("AIDP_ENV_FILE", raising=False)

    parser, _, env_path = connection_parser([], "Test settings.", missing)

    assert env_path == missing
    assert parser.parse_args([]).workspace_name is None


def test_runtime_dependency_pins_match_requirements_file():
    """PEP 621 metadata matches direct runtime requirement pins offline."""
    root = Path(__file__).resolve().parents[2]
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    metadata_dependencies = set(metadata["project"]["dependencies"])
    requirements = set()
    for line in (root / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-r"):
            continue
        if line.endswith(".whl"):
            match = re.search(r"aidp_python_client-([\d.]+)-", line)
            assert match
            requirements.add(f"aidp-python-client=={match.group(1)}")
        else:
            requirements.add(line)

    assert metadata_dependencies == requirements


def test_real_api_key_profile_and_region_override(tmp_path):
    """Use a temporary generated key to test the actual OCI config and signer."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_path = tmp_path / "test.pem"
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    config_path = tmp_path / "config"
    config_path.write_text(
        "[TEST]\nuser=ocid1.user.oc1..example\ntenancy=ocid1.tenancy.oc1..example\n"
        f"fingerprint={'aa:' * 15}aa\nkey_file={key_path}\nregion=us-ashburn-1\n",
        encoding="utf-8",
    )
    args = SimpleNamespace(
        config_file=str(config_path), profile="TEST", region="eu-frankfurt-1"
    )
    config, options = load_auth(args)
    assert config["region"] == "eu-frankfurt-1"
    assert isinstance(options["signer"], oci.signer.Signer)
    assert (
        options["signer"].private_key.public_key().public_numbers()
        == key.public_key().public_numbers()
    )
    assert options["timeout"] == (10, 30)
    assert isinstance(options["retry_strategy"], oci.retry.NoneRetryStrategy)


def test_sessions_close_on_partial_initialization_failure():
    """The first client is closed even when the next client constructor fails."""
    first = Mock()
    first.base_client.type_mappings = {"datetime": "original"}
    with pytest.raises(RuntimeError):
        with ExitStack() as resources:
            managed_client(
                resources, Mock(return_value=first), {}, {}, preserve_timestamps=True
            )
            managed_client(resources, Mock(side_effect=RuntimeError("failed")), {}, {})
    first.base_client.session.close.assert_called_once()
    assert first.base_client.session.max_redirects == 0
    assert first.base_client.type_mappings["datetime"] is object


def test_token_profiles_fail_before_key_loading(monkeypatch):
    """The common API-key authentication contract remains explicit."""
    monkeypatch.setattr(
        oci.config, "from_file", Mock(return_value={"security_token_file": "token"})
    )
    monkeypatch.setattr(oci.config, "validate_config", Mock())
    signer = Mock()
    monkeypatch.setattr(oci.signer, "Signer", signer)
    with pytest.raises(AidpError, match="API-key"):
        load_auth(
            SimpleNamespace(
                config_file="config", profile="DEFAULT", region="eu-frankfurt-1"
            )
        )
    signer.assert_not_called()
