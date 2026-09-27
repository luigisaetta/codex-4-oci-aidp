"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Cluster lifecycle MCP domain operations.
"""

from aidp_mcp.operations import AidpWorkflowOperations


def get_cluster_status(settings, cluster_name):
    """Delegate sanitized cluster status retrieval."""
    return AidpWorkflowOperations(settings).get_cluster_status(cluster_name)


def set_cluster_state(settings, cluster_name, action, **kwargs):
    """Delegate explicitly confirmed cluster lifecycle handling."""
    return AidpWorkflowOperations(settings).set_cluster_state(
        cluster_name, action, **kwargs
    )
