"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Offline tests for MCP remote-path and limit validation.
"""

import pytest

from aidp_common.connection import AidpError
from aidp_mcp import validation


@pytest.mark.parametrize(
    "value",
    [
        "/Workspace/jobs/example.ipynb",
        "/Workspace/../unsafe.ipynb",
        "jobs/example.txt",
        "../unsafe.ipynb",
    ],
)
def test_validate_workspace_path_rejects_unsafe_values(value):
    """Only notebook paths below the workspace root are accepted."""
    with pytest.raises(AidpError):
        validation.validate_workspace_path(value)


def test_validate_workspace_path_normalizes_valid_notebook_path():
    """A valid workspace-relative notebook path is retained."""
    assert (
        validation.validate_workspace_path("jobs/example.ipynb") == "jobs/example.ipynb"
    )


@pytest.mark.parametrize("value", ["Workspace", "/Shared", "/Workspace/../unsafe", ""])
def test_validate_workspace_directory_rejects_unsafe_values(value):
    """Notebook listing is restricted to an absolute workspace directory."""
    with pytest.raises(AidpError):
        validation.validate_workspace_directory(value)


def test_validate_workspace_directory_accepts_workspace_root():
    """The workspace root is an allowed read-only listing target."""
    assert validation.validate_workspace_directory("/Workspace") == "/Workspace"


@pytest.mark.parametrize("value", ["files", "/files/../secret", "", True])
def test_validate_volume_path_rejects_unsafe_values(value):
    """Volume exploration accepts only absolute traversal-free POSIX paths."""
    with pytest.raises(AidpError):
        validation.validate_volume_path(value)


def test_validate_volume_path_normalizes_root_and_children():
    """The volume root and ordinary child path are accepted."""
    assert validation.validate_volume_path("/") == "/"
    assert validation.validate_volume_path("/reports/2026") == "/reports/2026"


@pytest.mark.parametrize("value", ["notebooks/test00.ipynb", "/Workspace/jobs"])
def test_validate_workspace_notebook_path_rejects_nonabsolute_or_nonnotebook(value):
    """Job discovery accepts only one safe absolute notebook path."""
    with pytest.raises(AidpError):
        validation.validate_workspace_notebook_path(value)


def test_normalized_task_notebook_path_accepts_relative_sdk_task_path():
    """Job task paths stored relative by the SDK match workspace discovery."""
    assert (
        getattr(validation, "_normalized_task_notebook_path")(
            "notebooks/test00/test00.ipynb"
        )
        == "/Workspace/notebooks/test00/test00.ipynb"
    )


def test_notebook_service_path_is_absolute_and_url_encoded():
    """Notebook API paths preserve the service root and encode path separators."""
    content_path = validation.workspace_content_path("jobs/example.ipynb")

    assert content_path == "/Workspace/jobs/example.ipynb"
    assert (
        validation.encoded_content_path(content_path)
        == "%2FWorkspace%2Fjobs%2Fexample.ipynb"
    )
