"""
Author: L. Saetta
Date last modified: 2026-09-15
License: MIT
Description: Offline tests for AI DP MCP validation and tool registration.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from aidp_common.connection import AidpError
from aidp_mcp import service
from aidp_mcp.server import MCP


def test_validate_local_notebook_returns_json_and_digest(tmp_path, monkeypatch):
    """A repository-local valid notebook returns parsed content and a digest."""
    monkeypatch.setattr(service, "PROJECT_ROOT", tmp_path)
    notebook = tmp_path / "example.ipynb"
    notebook.write_text(json.dumps({"cells": [], "nbformat": 4}), encoding="utf-8")

    path, content, digest = service.validate_local_notebook(notebook)

    assert path == notebook
    assert content["nbformat"] == 4
    assert len(digest) == 64


def test_validate_local_notebook_rejects_path_outside_repository(tmp_path, monkeypatch):
    """The MCP adapter cannot upload arbitrary local paths."""
    monkeypatch.setattr(service, "PROJECT_ROOT", tmp_path / "repository")
    outside = tmp_path / "outside.ipynb"
    outside.write_text("{}", encoding="utf-8")

    with pytest.raises(AidpError, match="inside the repository"):
        service.validate_local_notebook(outside)


@pytest.mark.parametrize(
    "value",
    [
        "relative.ipynb",
        "/Workspace/../unsafe.ipynb",
        "/Other/a.ipynb",
        "/Workspace/a.txt",
    ],
)
def test_validate_workspace_path_rejects_unsafe_values(value):
    """Only notebook paths below the workspace root are accepted."""
    with pytest.raises(AidpError):
        service.validate_workspace_path(value)


def test_validate_workspace_path_normalizes_valid_notebook_path():
    """A valid workspace notebook path is retained."""
    assert service.validate_workspace_path("/Workspace/jobs/example.ipynb") == (
        "/Workspace/jobs/example.ipynb"
    )


def test_supported_job_requires_one_workspace_notebook_task():
    """Job reconciliation rejects unrelated or multi-task job definitions."""
    supported = SimpleNamespace(
        tasks=[
            SimpleNamespace(
                type="NOTEBOOK_TASK", source="WORKSPACE", task_key="notebook"
            )
        ]
    )
    unsupported = SimpleNamespace(tasks=[SimpleNamespace(type="PYTHON_TASK")])

    assert service._is_supported_job(supported)
    assert not service._is_supported_job(unsupported)


def test_server_registers_only_the_specified_tools():
    """The MCP schema exposes the four scoped tools without cloud access."""
    names = {tool.name for tool in asyncio.run(MCP.list_tools())}
    assert names == {
        "upload_notebook",
        "ensure_notebook_job",
        "start_notebook_job",
        "get_job_run",
    }
