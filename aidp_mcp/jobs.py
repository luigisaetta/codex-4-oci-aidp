"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Workflow job and run MCP domain operations.
"""

from aidp_mcp.operations import AidpWorkflowOperations


def find_notebook_jobs(settings, workspace_notebook_path, max_results=100):
    """Delegate the scoped workflow-job search operation."""
    return AidpWorkflowOperations(settings).find_notebook_jobs(
        workspace_notebook_path, max_results
    )


def list_job_runs(settings, job_name=None, job_key=None, max_results=25):
    """Delegate bounded workflow-run listing."""
    return AidpWorkflowOperations(settings).list_job_runs(
        job_name, job_key, max_results
    )


def ensure_notebook_job(
    settings, job_name, workspace_notebook_path, cluster_name, **kwargs
):
    """Delegate managed notebook-job planning or reconciliation."""
    return AidpWorkflowOperations(settings).ensure_notebook_job(
        job_name, workspace_notebook_path, cluster_name, **kwargs
    )


def start_notebook_job(settings, job_name, **kwargs):
    """Delegate explicit notebook-job submission."""
    return AidpWorkflowOperations(settings).start_notebook_job(job_name, **kwargs)


def get_job_run(settings, job_run_key):
    """Delegate sanitized job-run status retrieval."""
    return AidpWorkflowOperations(settings).get_job_run(job_run_key)


def get_job_run_output(settings, job_run_key, max_characters=12000):
    """Delegate bounded job-run output retrieval."""
    return AidpWorkflowOperations(settings).get_job_run_output(
        job_run_key, max_characters
    )
