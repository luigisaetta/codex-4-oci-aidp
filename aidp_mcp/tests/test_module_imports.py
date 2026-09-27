"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Offline import-boundary checks for the MCP package.
"""

import subprocess
import sys


def test_mcp_modules_import_in_a_fresh_interpreter():
    """The intended MCP module graph has no import cycle."""
    modules = (
        "aidp_mcp.config",
        "aidp_mcp.local_files",
        "aidp_mcp.validation",
        "aidp_mcp.targets",
        "aidp_mcp.lookups",
        "aidp_mcp.safety",
        "aidp_mcp.notebooks",
        "aidp_mcp.jobs",
        "aidp_mcp.clusters",
        "aidp_mcp.volumes",
        "aidp_mcp.service",
        "aidp_mcp.server",
    )
    command = [sys.executable, "-c", f"import {', '.join(modules)}"]

    completed = subprocess.run(command, check=False, capture_output=True, text=True)

    assert completed.returncode == 0, completed.stderr
