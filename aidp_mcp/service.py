"""
Author: L. Saetta
Date last modified: 2026-09-15
License: MIT
Description: Validated AI DP notebook upload and single-task workflow operations.
"""

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import time

import oci
from aidp_python_client.aidataplatform_dp import (
    ClusterClient,
    NotebookClient,
    WorkflowClient,
    WorkspaceClient,
    models,
)

from aidp_common.connection import (
    AidpError,
    list_instances,
    load_auth,
    managed_client,
    resolve_compartment,
    validate_resource_key,
)
from aidp_common.settings import connection_parser, validate_connection

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TERMINAL_JOB_STATES = {"SUCCESS", "FAILED", "ERROR", "CANCELED", "TIMED_OUT"}


@dataclass(frozen=True)
class Target:
    """Resolved AI DP instance, workspace, and cluster identifiers."""

    instance_id: str
    workspace_key: str
    cluster_key: str
    cluster_name: str


def load_connection_settings():
    """Load and validate the shared project connection settings.

    Returns:
        argparse.Namespace: Validated shared connection settings.

    Raises:
        SystemExit: The project configuration is incomplete or invalid.
    """
    parser, _, _ = connection_parser([], "Run AI DP notebook workflow MCP tools.")
    args = parser.parse_args([])
    validate_connection(args, parser)
    if not args.workspace_name:
        parser.error("Set WORKSPACE_NAME before using AI DP MCP tools.")
    return args


def validate_local_notebook(local_path):
    """Read a local notebook safely and return its parsed JSON and digest.

    Args:
        local_path: Repository-relative or absolute notebook path.

    Returns:
        tuple[Path, dict, str]: Canonical path, parsed notebook JSON, SHA-256.

    Raises:
        AidpError: The path is unsafe, absent, not a notebook, or invalid JSON.
    """
    candidate = Path(local_path).expanduser().resolve()
    try:
        candidate.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise AidpError("Local notebook path must be inside the repository.") from exc
    if candidate.suffix != ".ipynb" or not candidate.is_file():
        raise AidpError("Local notebook must be an existing .ipynb file.")
    try:
        payload = candidate.read_bytes()
        content = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AidpError("Local notebook must contain valid UTF-8 JSON.") from exc
    if not isinstance(content, dict) or not content:
        raise AidpError("Local notebook JSON must be a nonempty object.")
    return candidate, content, hashlib.sha256(payload).hexdigest()


def validate_workspace_path(workspace_path):
    """Validate a workspace notebook path without allowing traversal.

    Args:
        workspace_path: Absolute POSIX path in the AI DP workspace.

    Returns:
        str: Normalized workspace path.

    Raises:
        AidpError: The path is not an absolute notebook path below /Workspace.
    """
    path = PurePosixPath(workspace_path)
    if (
        not workspace_path.startswith("/")
        or path.suffix != ".ipynb"
        or ".." in path.parts
        or len(path.parts) < 3
        or path.parts[1] != "Workspace"
    ):
        raise AidpError("Workspace path must be a .ipynb path below /Workspace.")
    return str(path)


def _resource_key(resource, label):
    value = getattr(resource, "key", None)
    if not isinstance(value, str) or not value:
        raise AidpError(f"{label} response is missing its key.")
    validate_resource_key(value)
    return value


def find_workspace(workspaces, instance_id, workspace_name):
    """Resolve one exact workspace name within an AI DP instance.

    Args:
        workspaces: Generated WorkspaceClient.
        instance_id: AI DP instance OCID.
        workspace_name: Exact workspace display name.

    Returns:
        str: Workspace key.

    Raises:
        AidpError: The workspace is absent or ambiguous.
    """
    matches = [
        item
        for item in oci.pagination.list_call_get_all_results(
            workspaces.list_workspaces, instance_id, display_name=workspace_name
        ).data
        if getattr(item, "display_name", None) == workspace_name
    ]
    if len(matches) != 1:
        raise AidpError(f"Workspace name has {len(matches)} visible matches.")
    return _resource_key(matches[0], "Workspace")


def find_cluster(clusters, instance_id, workspace_key, cluster_name):
    """Resolve one exact active cluster in a workspace.

    Args:
        clusters: Generated ClusterClient.
        instance_id: AI DP instance OCID.
        workspace_key: Workspace key.
        cluster_name: Exact cluster display name.

    Returns:
        Target: Resolved cluster target.

    Raises:
        AidpError: The cluster is absent, ambiguous, or not active.
    """
    matches = [
        item
        for item in oci.pagination.list_call_get_all_results(
            clusters.list_clusters,
            instance_id,
            workspace_key,
            display_name=cluster_name,
        ).data
        if getattr(item, "display_name", None) == cluster_name
    ]
    if len(matches) != 1:
        raise AidpError(f"Cluster name has {len(matches)} visible matches.")
    key = _resource_key(matches[0], "Cluster")
    cluster = clusters.get_cluster(instance_id, workspace_key, key).data
    if getattr(cluster, "state", None) != "ACTIVE":
        raise AidpError("Selected cluster must be ACTIVE before a job run.")
    return Target(instance_id, workspace_key, key, cluster_name)


def _find_job(workflows, instance_id, workspace_key, job_name):
    matches = [
        item
        for item in oci.pagination.list_call_get_all_results(
            workflows.list_jobs, instance_id, workspace_key, display_name=job_name
        ).data
        if getattr(item, "name", getattr(item, "display_name", None)) == job_name
    ]
    if len(matches) > 1:
        raise AidpError(f"Job name has {len(matches)} visible matches.")
    if not matches:
        return None
    return workflows.get_job(
        instance_id, workspace_key, _resource_key(matches[0], "Job")
    )


def _managed_task(notebook_path, cluster_key):
    cluster = models.JobCluster(cluster_key=cluster_key)
    return models.NotebookTask(
        task_key="notebook",
        notebook_path=notebook_path,
        source=models.NotebookTask.SOURCE_WORKSPACE,
        cluster=cluster,
        depends_on=[],
        parameters=[],
        max_retries=0,
        is_retry_on_timeout=False,
    )


def _is_supported_job(job):
    tasks = getattr(job, "tasks", None)
    if not isinstance(tasks, list) or len(tasks) != 1:
        return False
    task = tasks[0]
    return (
        getattr(task, "type", None) == "NOTEBOOK_TASK"
        and getattr(task, "source", None) == "WORKSPACE"
        and getattr(task, "task_key", None) == "notebook"
    )


class AidpWorkflowService:
    """Perform scoped notebook and workflow operations through typed SDK clients."""

    def __init__(self, settings=None):
        """Initialize the service without creating cloud clients.

        Args:
            settings: Optional validated shared settings for tests or execution.
        """
        self.settings = settings or load_connection_settings()

    @contextmanager
    def _clients(self):
        config, options = load_auth(self.settings)
        workbench_options = dict(options)
        if self.settings.endpoint:
            workbench_options["service_endpoint"] = self.settings.endpoint
        resources = ExitStack()
        identity = managed_client(
            resources, oci.identity.IdentityClient, config, options
        )
        control = managed_client(
            resources, oci.ai_data_platform.AiDataPlatformClient, config, options
        )
        workspaces = managed_client(
            resources, WorkspaceClient, config, workbench_options
        )
        clusters = managed_client(resources, ClusterClient, config, workbench_options)
        notebooks = managed_client(resources, NotebookClient, config, workbench_options)
        workflows = managed_client(resources, WorkflowClient, config, workbench_options)
        compartment = resolve_compartment(
            identity, config["tenancy"], self.settings.compartment
        )
        instances = list_instances(control, compartment, self.settings.instance_id)
        try:
            if len(instances) != 1:
                raise AidpError("Exactly one active AI DP instance must be selected.")
            instance_id = instances[0].id
            validate_resource_key(instance_id)
            workspace_key = find_workspace(
                workspaces, instance_id, self.settings.workspace_name
            )
            yield instance_id, workspace_key, clusters, notebooks, workflows
        finally:
            resources.close()

    def upload_notebook(self, local_path, workspace_path, overwrite=False, apply=False):
        """Plan or copy a local notebook to an AI DP workspace.

        Args:
            local_path: Local notebook path under the repository root.
            workspace_path: Destination notebook path under /Workspace.
            overwrite: Allow replacement of existing remote content.
            apply: Submit the update after the plan is reported.

        Returns:
            dict: Sanitized upload plan or result.

        Raises:
            AidpError: Validation or AI DP discovery/upload fails.
        """
        local_file, content, digest = validate_local_notebook(local_path)
        destination = validate_workspace_path(workspace_path)
        with self._clients() as clients:
            instance_id, workspace_key, _, notebooks, _ = clients
            try:
                remote = notebooks.get_content(
                    instance_id, workspace_key, destination
                ).data
                remote_digest = getattr(remote, "hash", None)
                same = (
                    getattr(remote, "hash_algorithm", "").lower().replace("-", "")
                    == "sha256"
                    and remote_digest == digest
                )
                action = "unchanged" if same else "update"
            except oci.exceptions.ServiceError as exc:
                if exc.status != 404:
                    raise
                action = "create"
            result = {
                "action": action,
                "apply": apply,
                "local_path": str(local_file.relative_to(PROJECT_ROOT)),
                "workspace_path": destination,
                "sha256": digest,
            }
            if not apply or action == "unchanged":
                return result
            if action == "update" and not overwrite:
                raise AidpError(
                    "Remote notebook exists; set overwrite=true to replace it."
                )
            notebooks.update_content(
                instance_id,
                workspace_key,
                destination,
                models.UpdateContentDetails(
                    name=PurePosixPath(destination).name,
                    path=destination,
                    type=models.UpdateContentDetails.TYPE_NOTEBOOK,
                    content=content,
                    format=models.UpdateContentDetails.FORMAT_JSON,
                ),
                retry_strategy=oci.retry.NoneRetryStrategy(),
            )
            return result

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
        if not isinstance(job_name, str) or not job_name.strip():
            raise AidpError("Job name must be nonempty.")
        if not isinstance(max_concurrent_runs, int) or max_concurrent_runs < 1:
            raise AidpError("max_concurrent_runs must be a positive integer.")
        notebook_path = validate_workspace_path(workspace_notebook_path)
        if (
            not job_location.startswith("/Workspace/")
            or ".." in PurePosixPath(job_location).parts
        ):
            raise AidpError("Job location must be below /Workspace.")
        with self._clients() as clients:
            instance_id, workspace_key, clusters, _, workflows = clients
            target = find_cluster(clusters, instance_id, workspace_key, cluster_name)
            response = _find_job(workflows, instance_id, workspace_key, job_name)
            action = "create" if response is None else "update"
            if response is not None and not _is_supported_job(response.data):
                raise AidpError("Existing job is not a managed single notebook task.")
            result = {
                "action": action,
                "apply": apply,
                "job_name": job_name,
                "workspace_path": notebook_path,
                "cluster_name": cluster_name,
            }
            if not apply:
                return result
            details = {
                "name": job_name,
                "description": "Managed by the AI DP MCP server.",
                "path": job_location,
                "max_concurrent_runs": max_concurrent_runs,
                "job_clusters": [models.JobCluster(cluster_key=target.cluster_key)],
                "tasks": [_managed_task(notebook_path, target.cluster_key)],
            }
            if response is None:
                created = workflows.create_job(
                    instance_id,
                    workspace_key,
                    models.CreateJobDetails(**details),
                    retry_strategy=oci.retry.NoneRetryStrategy(),
                )
                job_key = _resource_key(created.data, "Job")
                workflows.update_job(
                    instance_id,
                    workspace_key,
                    job_key,
                    models.UpdateJobDetails(**details),
                    retry_strategy=oci.retry.NoneRetryStrategy(),
                )
            else:
                job_key = _resource_key(response.data, "Job")
                workflows.update_job(
                    instance_id,
                    workspace_key,
                    job_key,
                    models.UpdateJobDetails(**details),
                    if_match=response.headers.get("etag"),
                    retry_strategy=oci.retry.NoneRetryStrategy(),
                )
            result["job_key"] = job_key
            return result

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
        if not confirm_start:
            raise AidpError(
                "Set confirm_start=true to submit a compute-consuming job run."
            )
        if not isinstance(timeout_seconds, int) or timeout_seconds < 1:
            raise AidpError("timeout_seconds must be a positive integer.")
        with self._clients() as clients:
            instance_id, workspace_key, clusters, _, workflows = clients
            response = _find_job(workflows, instance_id, workspace_key, job_name)
            if response is None or not _is_supported_job(response.data):
                raise AidpError("Job must be an existing managed single notebook task.")
            task = response.data.tasks[0]
            cluster_key = getattr(getattr(task, "cluster", None), "cluster_key", None)
            validate_resource_key(cluster_key)
            cluster = clusters.get_cluster(instance_id, workspace_key, cluster_key).data
            if getattr(cluster, "state", None) != "ACTIVE":
                raise AidpError("The job cluster must be ACTIVE before submission.")
            job_key = _resource_key(response.data, "Job")
            created = workflows.create_job_run(
                instance_id,
                workspace_key,
                models.CreateJobRunDetails(job_key=job_key, parameters=[]),
                retry_strategy=oci.retry.NoneRetryStrategy(),
            )
            run_key = _resource_key(created.data, "Job run")
            result = {
                "job_name": job_name,
                "job_run_key": run_key,
                "state": "SUBMITTED",
            }
            if not wait:
                return result
            return self._wait_for_run(
                workflows, instance_id, workspace_key, run_key, timeout_seconds
            )

    def _wait_for_run(
        self, workflows, instance_id, workspace_key, run_key, timeout_seconds
    ):
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            response = workflows.get_job_run(instance_id, workspace_key, run_key)
            state = _run_state(response.data)
            if state in TERMINAL_JOB_STATES:
                return _run_response(response.data, run_key)
            time.sleep(min(10, max(0.1, deadline - time.monotonic())))
        latest = workflows.get_job_run(instance_id, workspace_key, run_key).data
        result = _run_response(latest, run_key)
        result["timed_out"] = True
        return result

    def get_job_run(self, job_run_key):
        """Read a job run status without fetching logs or notebook output.

        Args:
            job_run_key: Existing AI DP job-run key.

        Returns:
            dict: Sanitized job-run status.
        """
        validate_resource_key(job_run_key)
        with self._clients() as clients:
            instance_id, workspace_key, _, _, workflows = clients
            response = workflows.get_job_run(instance_id, workspace_key, job_run_key)
            return _run_response(response.data, job_run_key)


def _run_state(job_run):
    state = getattr(job_run, "state", None)
    return getattr(state, "status", state)


def _run_response(job_run, run_key):
    state = getattr(job_run, "state", None)
    return {
        "job_run_key": run_key,
        "state": _run_state(job_run),
        "state_message": getattr(state, "state_message", None),
        "start_time": str(getattr(job_run, "start_time", "")) or None,
        "end_time": str(getattr(job_run, "end_time", "")) or None,
    }
