"""
Author: L. Saetta
Date last modified: 2026-09-15
License: MIT
Description: Stdio MCP server exposing scoped AI DP notebook workflow tools.
"""

from fastmcp import FastMCP

from aidp_mcp.service import AidpWorkflowService

MCP = FastMCP("aidp-mcp")


def _service():
    """Create one short-lived service per MCP request."""
    return AidpWorkflowService()


@MCP.tool()
def upload_notebook(
    local_path: str, workspace_path: str, overwrite: bool = False, apply: bool = False
) -> dict:
    """Plan or upload a local repository notebook to an AI DP workspace.

    `workspace_path` is relative to the selected workspace root. `apply=false`
    is read-only. `apply=true` creates or replaces content only when
    overwrite is explicitly true for an existing notebook.
    """
    return _service().upload_notebook(local_path, workspace_path, overwrite, apply)


@MCP.tool()
def ensure_notebook_job(
    job_name: str,
    workspace_notebook_path: str,
    cluster_name: str,
    *,
    job_location: str = "/Workspace/jobs",
    max_concurrent_runs: int = 1,
    apply: bool = False,
) -> dict:
    """Plan or reconcile a managed, single-notebook AI DP workflow job."""
    return _service().ensure_notebook_job(
        job_name,
        workspace_notebook_path,
        cluster_name,
        job_location=job_location,
        max_concurrent_runs=max_concurrent_runs,
        apply=apply,
    )


@MCP.tool()
def start_notebook_job(
    job_name: str,
    *,
    wait: bool = False,
    timeout_seconds: int = 1200,
    confirm_start: bool = False,
) -> dict:
    """Start a managed notebook job; confirm_start=true is required."""
    return _service().start_notebook_job(
        job_name,
        wait=wait,
        timeout_seconds=timeout_seconds,
        confirm_start=confirm_start,
    )


@MCP.tool()
def get_job_run(job_run_key: str) -> dict:
    """Read sanitized status for a previously submitted AI DP job run."""
    return _service().get_job_run(job_run_key)


@MCP.tool()
def get_cluster_status(cluster_name: str) -> dict:
    """Read the selected AI DP cluster's state and small configuration summary."""
    return _service().get_cluster_status(cluster_name)


@MCP.tool()
def get_job_run_output(job_run_key: str, max_characters: int = 12000) -> dict:
    """Read bounded plain-text output for a managed single-notebook job run."""
    return _service().get_job_run_output(job_run_key, max_characters)


def main():
    """Run the MCP server over standard input/output."""
    MCP.run(transport="stdio")


if __name__ == "__main__":
    main()
