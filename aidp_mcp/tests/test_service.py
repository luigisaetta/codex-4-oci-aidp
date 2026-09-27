"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Offline tests for AI DP MCP validation and tool registration.
"""

# pylint: disable=protected-access,too-many-lines

import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import oci
import pytest
from fastmcp import Client

from aidp_common.connection import AidpError
from aidp_mcp import local_files, service, targets
from aidp_mcp import server
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


def test_upload_plan_uses_matching_extra_root_without_remote_write(
    tmp_path, monkeypatch
):
    """A plan from a second root exposes only relative local location details."""
    root = tmp_path / "my-langgraph-agent"
    root.mkdir()
    notebook = root / "notebooks" / "example.ipynb"
    notebook.parent.mkdir()
    notebook.write_text('{"nbformat": 4}', encoding="utf-8")
    workflow_service = service.AidpWorkflowService(
        settings=SimpleNamespace(allowed_roots=(root,))
    )

    @contextmanager
    def clients():
        yield "instance", "workspace", Mock(), Mock(), Mock()

    missing = oci.exceptions.ServiceError(404, "NotFound", {}, "missing")
    request = Mock(side_effect=missing)
    monkeypatch.setattr(targets, "workspace_clients", lambda _settings: clients())
    monkeypatch.setattr(service, "notebook_content_request", request)

    result = workflow_service.upload_notebook(
        notebook, "plans/example.ipynb", apply=False
    )

    assert result == {
        "action": "create",
        "apply": False,
        "local_path": "notebooks/example.ipynb",
        "local_root": "my-langgraph-agent",
        "workspace_path": "plans/example.ipynb",
        "sha256": result["sha256"],
    }
    assert len(result["sha256"]) == 64
    assert request.call_args.kwargs["method"] == "GET"


def test_upload_notebook_validates_the_local_path_once(tmp_path, monkeypatch):
    """Upload reuses the matching root returned by notebook validation."""
    root = tmp_path / "allowed"
    root.mkdir()
    notebook = root / "example.ipynb"
    notebook.write_text('{"nbformat": 4}', encoding="utf-8")
    workflow_service = service.AidpWorkflowService(
        settings=SimpleNamespace(allowed_roots=(root,))
    )
    original = local_files.validate_local_path
    path_validation = Mock(wraps=original)
    missing = oci.exceptions.ServiceError(404, "NotFound", {}, "missing")

    @contextmanager
    def clients():
        yield "instance", "workspace", Mock(), Mock(), Mock()

    monkeypatch.setattr(targets, "workspace_clients", lambda _settings: clients())
    monkeypatch.setattr(local_files, "validate_local_path", path_validation)
    monkeypatch.setattr(service, "notebook_content_request", Mock(side_effect=missing))

    workflow_service.upload_notebook(notebook, "plans/example.ipynb", apply=False)

    assert path_validation.call_count == 1


def test_mcp_session_survives_a_configuration_error(monkeypatch):
    """A tool error from invalid settings does not close an in-memory session."""
    healthy_service = Mock()
    healthy_service.list_notebooks.return_value = {"notebooks": []}
    create_service = Mock(
        side_effect=[AidpError("Set WORKSPACE_NAME first."), healthy_service]
    )
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
    healthy_service.list_notebooks.assert_called_once_with("/Workspace", None, 100)


def test_workspace_objects_request_uses_notebook_filter_and_page_token():
    """Listing calls the documented workspace-object endpoint with safe filters."""
    notebooks = SimpleNamespace(base_client=Mock())

    service.workspace_objects_request(
        notebooks,
        instance_id="instance",
        workspace_key="workspace",
        path="/Workspace",
        limit=25,
        page="next-page",
    )

    arguments = notebooks.base_client.call_api.call_args.kwargs
    assert arguments["method"] == "GET"
    assert arguments["query_params"] == {
        "path": "/Workspace",
        "type": "NOTEBOOK",
        "limit": 25,
        "page": "next-page",
    }
    assert arguments["response_type"] == "WorkspaceObjectCollection"


def test_internal_error_for_missing_content_is_a_create_plan():
    """AI DP returns this specific error form when a notebook is absent."""
    error = SimpleNamespace(
        status=500,
        code="InternalError",
        message="Unexpected error while getting notebook content",
    )

    assert service.is_missing_content_error(error)


def test_directory_conflict_is_a_retained_workspace_folder():
    """AI DP's known directory conflict permits an idempotent upload retry."""
    error = SimpleNamespace(
        status=409,
        code="Conflict",
        message="Directory already exists",
    )

    assert service.is_existing_folder_error(error)


def test_mcp_workbench_clients_preserve_numeric_timestamps(monkeypatch):
    """All MCP AI DP clients retain numeric timestamps returned by the service."""
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


def test_list_notebooks_paginates_and_filters_metadata(monkeypatch):
    """Listing returns bounded matching summaries without notebook content."""
    workflow_service = service.AidpWorkflowService(settings=SimpleNamespace())
    notebooks = Mock()
    first_page = SimpleNamespace(
        data=SimpleNamespace(
            items=[
                SimpleNamespace(
                    display_name="Other.ipynb",
                    path="/Workspace/Other.ipynb",
                    type="NOTEBOOK",
                    time_created="first",
                    time_updated="first-update",
                    content="sensitive",
                )
            ]
        ),
        headers={"opc-next-page": "next-page"},
    )
    second_page = SimpleNamespace(
        data=SimpleNamespace(
            items=[
                SimpleNamespace(
                    display_name="test00.ipynb",
                    path="/Workspace/test00.ipynb",
                    type="NOTEBOOK",
                    time_created="second",
                    time_updated="second-update",
                    content="sensitive",
                )
            ]
        ),
        headers={},
    )

    @contextmanager
    def clients():
        yield "instance", "workspace", Mock(), notebooks, Mock()

    monkeypatch.setattr(targets, "workspace_clients", lambda _settings: clients())
    request = Mock(side_effect=[first_page, second_page])
    monkeypatch.setattr(service, "workspace_objects_request", request)

    result = workflow_service.list_notebooks(name_contains="TEST00", max_results=5)

    assert result == {
        "path": "/Workspace",
        "name_contains": "TEST00",
        "notebooks": [
            {
                "display_name": "test00.ipynb",
                "path": "/Workspace/test00.ipynb",
                "type": "NOTEBOOK",
                "time_created": "second",
                "time_updated": "second-update",
            }
        ],
        "is_truncated": False,
    }
    assert request.call_args_list[1].kwargs["page"] == "next-page"


@pytest.mark.parametrize("value", [0, 1001, True, "100"])
def test_list_notebooks_rejects_unsafe_result_limits(value):
    """Notebook listing validates its result limit before cloud discovery."""
    workflow_service = service.AidpWorkflowService(settings=SimpleNamespace())

    with pytest.raises(AidpError, match="max_results"):
        workflow_service.list_notebooks(max_results=value)


def test_server_registers_the_twelve_scoped_tools():
    """The MCP schema exposes the specified tools without cloud access."""
    names = {tool.name for tool in asyncio.run(MCP.list_tools())}
    assert names == {
        "upload_notebook",
        "list_notebooks",
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
