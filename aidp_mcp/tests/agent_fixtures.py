"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Shared offline fixtures for AI DP agent-domain tests.
"""

from types import SimpleNamespace


def make_agent(name="hello", key="agent-key", **values):
    """Build a representative AgentInfo SDK summary fixture."""
    defaults = {
        "display_name": name,
        "key": key,
        "type": "CODE",
        "lifecycle_state": "ACTIVE",
        "lifecycle_details": None,
        "deployment_mode": "MANUAL",
        "uri_state": "READY",
        "entry_file_path": "hello_agent.py",
        "dependencies_file_path": "requirements.txt",
        "path_info": "agents/hello",
        "compute_key": None,
        "deployment_compute_key": None,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


def collection_response(items, page=None):
    """Build an OCI-like collection response with an optional next-page token."""
    return SimpleNamespace(
        data=SimpleNamespace(items=items),
        headers={} if page is None else {"opc-next-page": page},
    )
