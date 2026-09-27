"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Stdio MCP server exposing scoped AI DP notebook, cluster, and volume tools.
"""

from fastmcp import FastMCP

from aidp_mcp import agents, clusters, jobs, notebooks, volumes
from aidp_mcp.config import load_connection_settings

MCP = FastMCP("aidp-mcp")


def _settings():
    """Load validated connection settings for one MCP tool request."""
    return load_connection_settings()


@MCP.tool()
def upload_notebook(
    local_path: str, workspace_path: str, overwrite: bool = False, apply: bool = False
) -> dict:
    """Plan or upload a local notebook from an allowed root to AI DP.

    `workspace_path` is relative to the selected workspace root. `apply=false`
    is read-only. `apply=true` creates or replaces content only when
    overwrite is explicitly true for an existing notebook. `local_path` may
    be relative only with the default repository upload root; otherwise it
    must be absolute so the server does not resolve it against its own
    repository.
    """
    return notebooks.upload_notebook(
        _settings(), local_path, workspace_path, overwrite, apply
    )


@MCP.tool()
def list_notebooks(
    path: str = "/Workspace",
    name_contains: str | None = None,
    max_results: int = 100,
) -> dict:
    """List notebook metadata in one AI DP workspace directory, without content.

    The optional name filter is a case-insensitive substring match. The path
    must be absolute and rooted at `/Workspace`; listing is non-recursive.
    """
    return notebooks.list_notebooks(_settings(), path, name_contains, max_results)


@MCP.tool()
def list_agents(name_contains: str | None = None, max_results: int = 50) -> dict:
    """List bounded, read-only metadata for configured-workspace agents.

    The optional name filter is a case-insensitive substring match. No agent
    code or deployment is changed.
    """
    return agents.list_agents(_settings(), name_contains, max_results)


@MCP.tool()
def get_agent(agent_name: str) -> dict:
    """Read one exact-name agent and its bounded deployment metadata.

    This read-only tool matches the agent display name exactly and
    case-sensitively; it does not change the agent or its deployments.
    """
    return agents.get_agent(_settings(), agent_name)


@MCP.tool()
def list_agent_sessions(agent_name: str, max_results: int = 25) -> dict:
    """List bounded, newest-first sessions for one exact-name agent.

    This read-only tool matches case-sensitively and returns metadata only.
    """
    return agents.list_agent_sessions(_settings(), agent_name, max_results)


@MCP.tool()
def get_agent_session_messages(
    agent_name: str, session_id: str, max_characters: int = 12000
) -> dict:
    """Read bounded messages for an exact-name agent session when authorized.

    This read-only tool matches agent names case-sensitively. Messages can
    contain application data, like job-run output; text is bounded and only
    metadata keys, never metadata values, are returned.
    """
    return agents.get_agent_session_messages(
        _settings(), agent_name, session_id, max_characters
    )


@MCP.tool()
def get_agent_trace(
    agent_name: str, session_id: str, trace_key: str, max_spans: int = 100
) -> dict:
    """Read bounded trace spans for one exact-name agent session.

    This read-only tool matches agent names case-sensitively. Prompt-bearing
    span attributes are never returned; only bounded error-event messages are.
    """
    return agents.get_agent_trace(
        _settings(), agent_name, session_id, trace_key, max_spans
    )


@MCP.tool()
def find_notebook_jobs(workspace_notebook_path: str, max_results: int = 100) -> dict:
    """List workflow jobs whose workspace notebook task uses one exact notebook.

    `workspace_notebook_path` must be absolute and rooted at `/Workspace`.
    This read-only search returns sanitized job and matching-task metadata.
    """
    return jobs.find_notebook_jobs(_settings(), workspace_notebook_path, max_results)


@MCP.tool()
def list_catalog_volumes(
    catalog_name: str, external_only: bool = True, max_results: int = 100
) -> dict:
    """List visible schemas and volumes in one exact AI DP catalog.

    By default only external Object Storage volumes are returned. Results are
    metadata only; no volume files or content are read.
    """
    return volumes.list_catalog_volumes(
        _settings(), catalog_name, external_only, max_results
    )


@MCP.tool()
def list_volume_files(
    catalog_name: str,
    schema_name: str,
    volume_name: str,
    path: str = "/",
    max_results: int = 100,
) -> dict:
    """Return a bounded recursive folder/file tree for one exact volume.

    All resource names are exact and case-sensitive. `path` must be absolute
    within the volume. The response contains metadata only, never file content.
    """
    return volumes.list_volume_files(
        _settings(),
        catalog_name,
        schema_name,
        volume_name,
        path=path,
        max_results=max_results,
    )


@MCP.tool()
def list_job_runs(
    job_name: str | None = None,
    job_key: str | None = None,
    max_results: int = 25,
) -> dict:
    """List newest-first, sanitized runs for one job selected by name or key.

    Provide exactly one selector. This read-only tool returns run keys, states,
    state messages, timestamps, and a truncation indicator.
    """
    return jobs.list_job_runs(_settings(), job_name, job_key, max_results)


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
    return jobs.ensure_notebook_job(
        _settings(),
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
    return jobs.start_notebook_job(
        _settings(),
        job_name,
        wait=wait,
        timeout_seconds=timeout_seconds,
        confirm_start=confirm_start,
    )


@MCP.tool()
def get_job_run(job_run_key: str) -> dict:
    """Read sanitized status for a previously submitted AI DP job run."""
    return jobs.get_job_run(_settings(), job_run_key)


@MCP.tool()
def get_cluster_status(cluster_name: str) -> dict:
    """Read the selected AI DP cluster's state and small configuration summary."""
    return clusters.get_cluster_status(_settings(), cluster_name)


@MCP.tool()
def set_cluster_state(
    cluster_name: str,
    action: str,
    *,
    wait: bool = False,
    timeout_seconds: int = 1200,
    confirm_action: bool = False,
) -> dict:
    """Explicitly start or stop one cluster; confirm_action=true is required.

    Start can incur compute charges; stop can interrupt workloads. An accepted
    request is not completion unless wait=true reports a completed outcome.
    """
    return clusters.set_cluster_state(
        _settings(),
        cluster_name,
        action,
        wait=wait,
        timeout_seconds=timeout_seconds,
        confirm_action=confirm_action,
    )


@MCP.tool()
def get_job_run_output(job_run_key: str, max_characters: int = 12000) -> dict:
    """Read bounded plain-text output for a managed single-notebook job run."""
    return jobs.get_job_run_output(_settings(), job_run_key, max_characters)


def main():
    """Run the MCP server over standard input/output."""
    MCP.run(transport="stdio")


if __name__ == "__main__":
    main()
