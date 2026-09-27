"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Offline tests for AI DP MCP notebook upload and listing operations.
"""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import oci
import pytest

from aidp_common.connection import AidpError
from aidp_mcp import local_files, notebooks, workspace_files


def test_upload_plan_uses_matching_extra_root_without_remote_write(
    tmp_path, monkeypatch
):
    """A plan from a second root exposes only relative local location details."""
    root = tmp_path / "my-langgraph-agent"
    root.mkdir()
    notebook = root / "notebooks" / "example.ipynb"
    notebook.parent.mkdir()
    notebook.write_text('{"nbformat": 4}', encoding="utf-8")
    settings = SimpleNamespace(allowed_roots=(root,))

    @contextmanager
    def clients():
        yield "instance", "workspace", Mock(), Mock(), Mock(), Mock()

    missing = oci.exceptions.ServiceError(404, "NotFound", {}, "missing")
    request = Mock(side_effect=missing)
    monkeypatch.setattr(notebooks, "workspace_clients", lambda _settings: clients())
    monkeypatch.setattr(notebooks, "notebook_content_request", request)

    result = notebooks.upload_notebook(
        settings, notebook, "plans/example.ipynb", apply=False
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
    settings = SimpleNamespace(allowed_roots=(root,))
    original = local_files.validate_local_path
    path_validation = Mock(wraps=original)
    missing = oci.exceptions.ServiceError(404, "NotFound", {}, "missing")

    @contextmanager
    def clients():
        yield "instance", "workspace", Mock(), Mock(), Mock(), Mock()

    monkeypatch.setattr(notebooks, "workspace_clients", lambda _settings: clients())
    monkeypatch.setattr(local_files, "validate_local_path", path_validation)
    monkeypatch.setattr(
        notebooks, "notebook_content_request", Mock(side_effect=missing)
    )

    notebooks.upload_notebook(settings, notebook, "plans/example.ipynb", apply=False)

    assert path_validation.call_count == 1


def test_workspace_objects_request_uses_notebook_filter_and_page_token():
    """Listing calls the documented workspace-object endpoint with safe filters."""
    notebook_client = SimpleNamespace(base_client=Mock())

    notebooks.workspace_objects_request(
        notebook_client,
        instance_id="instance",
        workspace_key="workspace",
        path="/Workspace",
        limit=25,
        page="next-page",
    )

    arguments = notebook_client.base_client.call_api.call_args.kwargs
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

    assert notebooks.is_missing_content_error(error)


def test_directory_conflict_is_a_retained_workspace_folder():
    """AI DP's known directory conflict permits an idempotent upload retry."""
    error = SimpleNamespace(
        status=409,
        code="Conflict",
        message="Directory already exists",
    )

    assert workspace_files.is_existing_folder_error(error)


def test_list_notebooks_paginates_and_filters_metadata(monkeypatch):
    """Listing returns bounded matching summaries without notebook content."""
    settings = SimpleNamespace()
    notebook_client = Mock()
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
        yield "instance", "workspace", Mock(), notebook_client, Mock(), Mock()

    monkeypatch.setattr(notebooks, "workspace_clients", lambda _settings: clients())
    request = Mock(side_effect=[first_page, second_page])
    monkeypatch.setattr(notebooks, "workspace_objects_request", request)

    result = notebooks.list_notebooks(settings, name_contains="TEST00", max_results=5)

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
    settings = SimpleNamespace()

    with pytest.raises(AidpError, match="max_results"):
        notebooks.list_notebooks(settings, max_results=value)
