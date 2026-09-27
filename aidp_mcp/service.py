"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Validated AI DP MCP notebook, workflow, cluster, catalog, and
volume operations.
"""

from aidp_mcp import clusters, jobs, notebooks, volumes
from aidp_mcp.config import load_connection_settings


class AidpWorkflowService:
    """Perform scoped notebook and workflow operations through typed SDK clients."""

    def __init__(self, settings=None):
        """Initialize the service without creating cloud clients.

        Args:
            settings: Optional validated shared settings for tests or execution.
        """
        self.settings = settings or load_connection_settings()

    def list_catalog_volumes(self, catalog_name, external_only=True, max_results=100):
        """List visible volumes below one exact catalog name."""
        return volumes.list_catalog_volumes(
            self.settings, catalog_name, external_only, max_results
        )

    def list_volume_files(
        self, catalog_name, schema_name, volume_name, path="/", max_results=100
    ):
        """List a bounded recursive folder and file tree in one volume."""
        return volumes.list_volume_files(
            self.settings,
            catalog_name,
            schema_name,
            volume_name,
            path=path,
            max_results=max_results,
        )

    def upload_notebook(self, local_path, workspace_path, overwrite=False, apply=False):
        """Plan or copy a local notebook to an AI DP workspace.

        Args:
            local_path: Local notebook path under a configured allowed root.
            workspace_path: Destination notebook path relative to the workspace root.
            overwrite: Allow replacement of existing remote content.
            apply: Submit the update after the plan is reported.

        Returns:
            dict: Sanitized upload plan or result.

        Raises:
            AidpError: Validation or AI DP discovery/upload fails.
        """
        return notebooks.upload_notebook(
            self.settings, local_path, workspace_path, overwrite, apply
        )

    def list_notebooks(self, path="/Workspace", name_contains=None, max_results=100):
        """List a workspace directory's notebook summaries without content.

        Args:
            path: Absolute workspace directory, rooted at ``/Workspace``.
            name_contains: Optional case-insensitive substring to match locally.
            max_results: Maximum notebook summaries returned across OCI pages.

        Returns:
            dict: Sanitized notebook summaries and truncation metadata.

        Raises:
            AidpError: The inputs are unsafe or the AI DP request fails.
        """
        return notebooks.list_notebooks(self.settings, path, name_contains, max_results)

    def find_notebook_jobs(self, workspace_notebook_path, max_results=100):
        """Find workflow jobs with a workspace task for one exact notebook.

        Args:
            workspace_notebook_path: Absolute notebook path rooted at
                ``/Workspace``.
            max_results: Maximum matching jobs returned across OCI pages.

        Returns:
            dict: Sanitized matching job and task metadata with truncation state.

        Raises:
            AidpError: The inputs are unsafe or AI DP job discovery fails.
        """
        return jobs.find_notebook_jobs(
            self.settings, workspace_notebook_path, max_results
        )

    def list_job_runs(self, job_name=None, job_key=None, max_results=25):
        """List bounded, newest-first run summaries for one workflow job.

        Exactly one job selector is required. A name is resolved to an exact
        visible job before the run query; a key is used directly after local
        validation.

        Args:
            job_name: Exact workflow job name, as an alternative to ``job_key``.
            job_key: Existing workflow job key, as an alternative to ``job_name``.
            max_results: Maximum run summaries returned across OCI pages.

        Returns:
            dict: Selected job reference, sanitized run summaries, and a
            truncation indicator.

        Raises:
            AidpError: The selectors are invalid or ambiguous, or the AI DP
                request fails.
        """
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
        """Plan or reconcile a managed single-notebook workflow job.

        Args:
            job_name: Exact workflow job name.
            workspace_notebook_path: Existing workspace notebook path.
            cluster_name: Exact active cluster name.
            job_location: Workspace path where AI DP stores the job definition.
            max_concurrent_runs: Positive job concurrency limit.
            apply: Submit the create or update after the plan is reported.

        Returns:
            dict: Sanitized job plan or result.

        Raises:
            AidpError: The requested job is unsafe or cannot be reconciled.
        """
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
        """Start a compatible notebook job and optionally poll its run.

        Args:
            job_name: Exact managed workflow job name.
            wait: Poll the run until a terminal state or timeout.
            timeout_seconds: Positive wait limit in seconds.
            confirm_start: Required explicit authorization for compute use.

        Returns:
            dict: Job-run key and latest known state.

        Raises:
            AidpError: Confirmation, job shape, cluster state, or polling fails.
        """
        return jobs.start_notebook_job(
            self.settings,
            job_name,
            wait=wait,
            timeout_seconds=timeout_seconds,
            confirm_start=confirm_start,
        )

    def get_job_run(self, job_run_key):
        """Read a job run status without fetching logs or notebook output.

        Args:
            job_run_key: Existing AI DP job-run key.

        Returns:
            dict: Sanitized job-run status.
        """
        return jobs.get_job_run(self.settings, job_run_key)

    def get_cluster_status(self, cluster_name):
        """Read a sanitized summary for one exact configured-workspace cluster.

        Args:
            cluster_name: Exact cluster display name in the configured workspace.

        Returns:
            dict: Cluster state and selected non-sensitive configuration fields.

        Raises:
            AidpError: The cluster is absent, ambiguous, or has an invalid name.
        """
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
        """Start or stop one exact cluster in the configured workspace.

        Args:
            cluster_name: Exact cluster display name in the configured workspace.
            action: Requested lifecycle action, either ``start`` or ``stop``.
            wait: Poll until the requested state is observed.
            timeout_seconds: Positive maximum polling duration in seconds.
            confirm_action: Required explicit authorization for the mutation.

        Returns:
            dict: Sanitized cluster summary and lifecycle submission outcome.

        Raises:
            AidpError: Validation, state transition, submission, or polling fails.
        """
        return clusters.set_cluster_state(
            self.settings,
            cluster_name,
            action,
            wait=wait,
            timeout_seconds=timeout_seconds,
            confirm_action=confirm_action,
        )

    def get_job_run_output(
        self, job_run_key, max_characters=jobs.MAX_JOB_RUN_OUTPUT_CHARACTERS
    ):
        """Fetch bounded plain-text output for a managed single-task job run.

        Args:
            job_run_key: Existing AI DP job-run key.
            max_characters: Combined output and error-trace character limit.

        Returns:
            dict: Sanitized task output metadata and bounded plain text.

        Raises:
            AidpError: The run is not a single-task run or has no output yet.
        """
        return jobs.get_job_run_output(self.settings, job_run_key, max_characters)
