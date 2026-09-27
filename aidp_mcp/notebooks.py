"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Notebook upload and listing MCP domain operations.
"""

from aidp_mcp.operations import AidpWorkflowOperations


def upload_notebook(settings, local_path, workspace_path, overwrite=False, apply=False):
    """Delegate the scoped notebook upload operation with validated settings."""
    return AidpWorkflowOperations(settings).upload_notebook(
        local_path, workspace_path, overwrite, apply
    )


def list_notebooks(settings, path="/Workspace", name_contains=None, max_results=100):
    """Delegate bounded notebook listing with validated settings."""
    return AidpWorkflowOperations(settings).list_notebooks(
        path, name_contains, max_results
    )
