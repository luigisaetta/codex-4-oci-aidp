"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Catalog-volume discovery MCP domain operations.
"""

from aidp_mcp.operations import AidpWorkflowOperations


def list_catalog_volumes(settings, catalog_name, external_only=True, max_results=100):
    """Delegate bounded catalog volume listing."""
    return AidpWorkflowOperations(settings).list_catalog_volumes(
        catalog_name, external_only, max_results
    )


def list_volume_files(
    settings, catalog_name, schema_name, volume_name, *, path="/", max_results=100
):
    """Delegate bounded volume-file tree discovery."""
    return AidpWorkflowOperations(settings).list_volume_files(
        catalog_name,
        schema_name,
        volume_name,
        path=path,
        max_results=max_results,
    )
