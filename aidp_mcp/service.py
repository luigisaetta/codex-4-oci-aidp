"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Facade for scoped AI DP MCP operations.
"""

from aidp_mcp import clusters, jobs, notebooks, volumes
from aidp_mcp.operations import load_connection_settings


class AidpWorkflowService:
    """Perform scoped AI DP operations through cohesive domain modules."""

    def __init__(self, settings=None):
        """Initialize the service without creating cloud clients.

        Args:
            settings: Optional validated shared settings for tests or execution.
        """
        self.settings = settings or load_connection_settings()

    def upload_notebook(self, local_path, workspace_path, overwrite=False, apply=False):
        """Plan or copy a local notebook to an AI DP workspace."""
        return notebooks.upload_notebook(
            self.settings, local_path, workspace_path, overwrite, apply
        )

    def list_notebooks(self, path="/Workspace", name_contains=None, max_results=100):
        """List a workspace directory's notebook summaries without content."""
        return notebooks.list_notebooks(self.settings, path, name_contains, max_results)

    def find_notebook_jobs(self, workspace_notebook_path, max_results=100):
        """Find workflow jobs with a workspace task for one exact notebook."""
        return jobs.find_notebook_jobs(
            self.settings, workspace_notebook_path, max_results
        )

    def list_catalog_volumes(self, catalog_name, external_only=True, max_results=100):
        """List visible volumes below one exact catalog name."""
        return volumes.list_catalog_volumes(
            self.settings, catalog_name, external_only, max_results
        )

    def list_volume_files(
        self, catalog_name, schema_name, volume_name, path="/", max_results=100
    ):
        """List a bounded recursive file tree for one exact catalog volume."""
        return volumes.list_volume_files(
            self.settings,
            catalog_name,
            schema_name,
            volume_name,
            path=path,
            max_results=max_results,
        )

    def list_job_runs(self, job_name=None, job_key=None, max_results=25):
        """List bounded, newest-first run summaries for one workflow job."""
        return jobs.list_job_runs(self.settings, job_name, job_key, max_results)

    def ensure_notebook_job(
        self,
        job_name,
        workspace_notebook_path,
        cluster_name,
        *,
        job_location="/Workspace/jobs",
        max_concurrent_runs=1,
        apply=False,
    ):
        """Plan or reconcile a managed single-notebook workflow job."""
        return jobs.ensure_notebook_job(
            self.settings,
            job_name,
            workspace_notebook_path,
            cluster_name,
            job_location=job_location,
            max_concurrent_runs=max_concurrent_runs,
            apply=apply,
        )

    def start_notebook_job(
        self, job_name, *, wait=False, timeout_seconds=1200, confirm_start=False
    ):
        """Start a compatible notebook job and optionally poll its run."""
        return jobs.start_notebook_job(
            self.settings,
            job_name,
            wait=wait,
            timeout_seconds=timeout_seconds,
            confirm_start=confirm_start,
        )

    def get_job_run(self, job_run_key):
        """Read a job run status without fetching logs or notebook output."""
        return jobs.get_job_run(self.settings, job_run_key)

    def get_cluster_status(self, cluster_name):
        """Read a sanitized summary for one exact configured-workspace cluster."""
        return clusters.get_cluster_status(self.settings, cluster_name)

    def set_cluster_state(
        self,
        cluster_name,
        action,
        *,
        wait=False,
        timeout_seconds=1200,
        confirm_action=False,
    ):
        """Start or stop one exact cluster in the configured workspace."""
        return clusters.set_cluster_state(
            self.settings,
            cluster_name,
            action,
            wait=wait,
            timeout_seconds=timeout_seconds,
            confirm_action=confirm_action,
        )

    def get_job_run_output(self, job_run_key, max_characters=12000):
        """Fetch bounded plain-text output for a managed single-task job run."""
        return jobs.get_job_run_output(self.settings, job_run_key, max_characters)
