"""
Author: L. Saetta
Date last modified: 2026-09-15
License: MIT
Description: Shared authentication and managed-client resource tests.
"""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock

import oci
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from aidp_common.connection import AidpError, load_auth, managed_client


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
