"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Offline tests for MCP connection settings loading.
"""

import asyncio
from unittest.mock import Mock

import pytest
from fastmcp import Client

from aidp_common import settings as common_settings
from aidp_common.connection import AidpError
from aidp_mcp import config, server, service
from aidp_mcp.server import MCP


def _mcp_settings_from_env(monkeypatch, env_file):
    """Load MCP settings from one test-only dotenv file."""
    monkeypatch.setattr(
        config,
        "connection_parser",
        lambda argv, description, parser_class: common_settings.connection_parser(
            argv, description, env_file, parser_class
        ),
    )
    return config.load_connection_settings()


def test_load_connection_settings_converts_missing_workspace_to_aidp_error(
    tmp_path, monkeypatch
):
    """Incomplete MCP settings return an error instead of ending the server."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "COMPARTMENT=compartment\nOCI_CONFIG_FILE=config\nOCI_PROFILE=DEFAULT\n"
        "REGION=eu-frankfurt-1\n",
        encoding="utf-8",
    )

    with pytest.raises(AidpError, match="WORKSPACE_NAME"):
        _mcp_settings_from_env(monkeypatch, env_file)


def test_load_connection_settings_converts_invalid_region_to_aidp_error(
    tmp_path, monkeypatch
):
    """Argparse region validation remains actionable without SystemExit."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "COMPARTMENT=compartment\nOCI_CONFIG_FILE=config\nOCI_PROFILE=DEFAULT\n"
        "REGION=not-a-region\nWORKSPACE_NAME=workspace\n",
        encoding="utf-8",
    )

    with pytest.raises(AidpError, match="REGION"):
        _mcp_settings_from_env(monkeypatch, env_file)


def test_mcp_parser_converts_missing_explicit_env_file_to_aidp_error(tmp_path):
    """Parser setup errors remain tool errors when the MCP parser is selected."""
    missing = tmp_path / "missing.env"

    with pytest.raises(AidpError, match="selected by --env-file"):
        common_settings.connection_parser(
            ["--env-file", str(missing)],
            "Test MCP settings.",
            parser_class=config.McpArgumentParser,
        )


def test_mcp_settings_file_error_hides_path_and_keeps_session_alive(
    tmp_path, monkeypatch
):
    """An absent process-selected dotenv remains one nonfatal MCP tool error."""
    missing = tmp_path / "missing.env"
    monkeypatch.setenv("AIDP_ENV_FILE", str(missing))
    healthy_service = Mock()
    healthy_service.list_notebooks.return_value = {"notebooks": []}
    first_call = True

    def create_service():
        nonlocal first_call
        if first_call:
            first_call = False
            return service.AidpWorkflowService()
        return healthy_service

    monkeypatch.setattr(server, "_service", create_service)

    async def call_tools():
        async with Client(MCP) as client:
            invalid = await client.call_tool("list_notebooks", raise_on_error=False)
            healthy = await client.call_tool("list_notebooks", raise_on_error=False)
        return invalid, healthy

    invalid_result, healthy_result = asyncio.run(call_tools())

    assert invalid_result.is_error
    assert "AIDP_ENV_FILE" in invalid_result.content[0].text
    assert str(missing) not in invalid_result.content[0].text
    assert not healthy_result.is_error


def test_mcp_selected_settings_without_workspace_keeps_session_alive(
    tmp_path, monkeypatch
):
    """A selected dotenv missing WORKSPACE_NAME produces one recoverable error."""
    settings_file = tmp_path / "selected.env"
    settings_file.write_text(
        "COMPARTMENT=compartment\nOCI_CONFIG_FILE=config\nOCI_PROFILE=DEFAULT\n"
        "REGION=eu-frankfurt-1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AIDP_ENV_FILE", str(settings_file))
    healthy_service = Mock()
    healthy_service.list_notebooks.return_value = {"notebooks": []}
    first_call = True

    def create_service():
        nonlocal first_call
        if first_call:
            first_call = False
            return service.AidpWorkflowService()
        return healthy_service

    monkeypatch.setattr(server, "_service", create_service)

    async def call_tools():
        async with Client(MCP) as client:
            invalid = await client.call_tool("list_notebooks", raise_on_error=False)
            healthy = await client.call_tool("list_notebooks", raise_on_error=False)
        return invalid, healthy

    invalid_result, healthy_result = asyncio.run(call_tools())

    assert invalid_result.is_error
    assert "WORKSPACE_NAME" in invalid_result.content[0].text
    assert not healthy_result.is_error


def test_mcp_missing_default_settings_names_root_env_and_selector(
    tmp_path, monkeypatch
):
    """No available MCP settings tells the operator both supported locations."""
    missing_default = tmp_path / "missing-default.env"
    monkeypatch.delenv("AIDP_ENV_FILE", raising=False)
    monkeypatch.setattr(
        config,
        "connection_parser",
        lambda argv, description, parser_class: common_settings.connection_parser(
            argv, description, missing_default, parser_class
        ),
    )

    with pytest.raises(AidpError) as error:
        config.load_connection_settings()

    assert "root .env" in str(error.value)
    assert "AIDP_ENV_FILE" in str(error.value)


def test_process_allowed_roots_overrides_dotenv_value(tmp_path, monkeypatch):
    """Process configuration keeps its documented precedence over dotenv."""
    dotenv_root = tmp_path / "dotenv-root"
    environment_root = tmp_path / "environment-root"
    dotenv_root.mkdir()
    environment_root.mkdir()
    env_file = tmp_path / ".env"
    env_file.write_text(
        "COMPARTMENT=compartment\nOCI_CONFIG_FILE=config\nOCI_PROFILE=DEFAULT\n"
        "REGION=eu-frankfurt-1\nWORKSPACE_NAME=workspace\n"
        f"AIDP_ALLOWED_ROOTS={dotenv_root}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AIDP_ALLOWED_ROOTS", str(environment_root))

    loaded = _mcp_settings_from_env(monkeypatch, env_file)

    assert loaded.allowed_roots == (environment_root.resolve(),)
