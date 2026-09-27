"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Offline tests for the AI DP MCP adapter and tool contract.
"""

import asyncio
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastmcp import Client

from aidp_common.connection import AidpError
from aidp_mcp import config, notebooks, server, targets
from aidp_mcp.server import MCP


@pytest.fixture(autouse=True)
def clear_process_target_cache():
    """Keep module-level target resolution state out of unrelated tests."""
    targets.clear_target_cache()
    yield
    targets.clear_target_cache()


def test_server_main_is_callable_without_arguments():
    """The console-script target imports without entering the stdio loop."""
    assert callable(server.main)


def test_mcp_session_survives_a_configuration_error(tmp_path, monkeypatch):
    """A selected-settings error does not close an in-memory MCP session."""
    missing = tmp_path / "missing.env"
    monkeypatch.setenv("AIDP_ENV_FILE", str(missing))
    settings = SimpleNamespace()
    list_notebooks = Mock(return_value={"notebooks": []})
    first_call = True

    def load_settings_once():
        nonlocal first_call
        if first_call:
            first_call = False
            return config.load_connection_settings()
        return settings

    monkeypatch.setattr(
        server,
        "_settings",
        load_settings_once,
    )
    monkeypatch.setattr(notebooks, "list_notebooks", list_notebooks)

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
    list_notebooks.assert_called_once_with(settings, "/Workspace", None, 100)


def test_mcp_workbench_clients_preserve_numeric_timestamps(monkeypatch):
    """All MCP AI DP clients retain numeric timestamps returned by the adapter."""
    settings = SimpleNamespace(
        endpoint=None, compartment="compartment", instance_id=None
    )
    managed = Mock(side_effect=[Mock() for _ in range(6)])

    monkeypatch.setattr(
        targets, "load_auth", Mock(return_value=({"tenancy": "tenancy"}, {}))
    )
    monkeypatch.setattr(targets, "managed_client", managed)
    monkeypatch.setattr(
        targets, "resolve_compartment", Mock(return_value="compartment")
    )
    monkeypatch.setattr(targets, "list_instances", Mock(return_value=[]))
    monkeypatch.setattr(targets, "ExitStack", Mock(return_value=Mock()))

    with pytest.raises(AidpError, match="Exactly one active"):
        with targets.workspace_clients(settings):
            pass

    assert [
        call.kwargs.get("preserve_timestamps") for call in managed.call_args_list
    ] == [None, None]


def test_server_registers_the_fourteen_scoped_tools():
    """The MCP schema exposes the specified tools without cloud access."""
    names = {tool.name for tool in asyncio.run(MCP.list_tools())}

    assert names == {
        "upload_notebook",
        "list_notebooks",
        "list_agents",
        "get_agent",
        "find_notebook_jobs",
        "list_catalog_volumes",
        "list_volume_files",
        "list_job_runs",
        "ensure_notebook_job",
        "start_notebook_job",
        "get_job_run",
        "get_cluster_status",
        "set_cluster_state",
        "get_job_run_output",
    }


def test_mcp_tool_contract_matches_snapshot():
    """The public tool names, descriptions, and schemas remain deliberate."""
    expected = json.loads(
        (Path(__file__).parent / "fixtures" / "mcp_tools.json").read_text(
            encoding="utf-8"
        )
    )
    tools = asyncio.run(MCP.list_tools())
    actual = [
        {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.parameters,
        }
        for tool in tools
    ]

    assert actual == expected


def test_aidp_mcp_modules_import_in_a_fresh_interpreter():
    """Every package module imports without a hidden circular dependency."""
    module_names = (
        "aidp_mcp.config",
        "aidp_mcp.local_files",
        "aidp_mcp.validation",
        "aidp_mcp.lookups",
        "aidp_mcp.safety",
        "aidp_mcp.targets",
        "aidp_mcp.agents",
        "aidp_mcp.notebooks",
        "aidp_mcp.jobs",
        "aidp_mcp.clusters",
        "aidp_mcp.volumes",
        "aidp_mcp.server",
    )
    command = (
        "import importlib; "
        f"[importlib.import_module(name) for name in {module_names!r}]"
    )
    result = subprocess.run(
        [sys.executable, "-c", command],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
