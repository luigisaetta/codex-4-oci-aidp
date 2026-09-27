"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: AI DP managed notebook-job discovery, lifecycle, runs, and output.
"""

from pathlib import PurePosixPath
import time

import oci
from aidp_python_client.aidataplatform_dp import models

from aidp_common.connection import AidpError, validate_resource_key
from aidp_mcp.lookups import SDK_PAGE_SIZE, find_cluster, next_page, resource_key
from aidp_mcp.safety import require_confirmation, should_apply
from aidp_mcp.targets import workspace_clients
from aidp_mcp.validation import (
    _normalized_task_notebook_path,
    validate_workspace_notebook_path,
    validate_workspace_path,
)

TERMINAL_JOB_STATES = {"SUCCESS", "FAILED", "ERROR", "CANCELED", "TIMED_OUT"}
MAX_JOB_RUN_OUTPUT_CHARACTERS = 12000
MAX_JOB_RUN_LIST_RESULTS = 1000
MAX_NOTEBOOK_JOB_SEARCH_RESULTS = 1000


def find_notebook_jobs(settings, workspace_notebook_path, max_results=100):
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
    notebook_path = validate_workspace_notebook_path(workspace_notebook_path)
    if (
        isinstance(max_results, bool)
        or not isinstance(max_results, int)
        or not 1 <= max_results <= MAX_NOTEBOOK_JOB_SEARCH_RESULTS
    ):
        raise AidpError("max_results must be an integer from 1 through 1000.")

    matches = []
    page = None
    with workspace_clients(settings) as clients:
        instance_id, workspace_key, _, _, workflows, _ = clients
        while len(matches) < max_results:
            response = workflows.list_jobs(
                instance_id,
                workspace_key,
                limit=min(SDK_PAGE_SIZE, max_results - len(matches)),
                page=page,
            )
            for summary in getattr(response.data, "items", None) or []:
                job_key = resource_key(summary, "Job")
                job = workflows.get_job(instance_id, workspace_key, job_key).data
                matching_tasks = _matching_notebook_tasks(job, notebook_path)
                if not matching_tasks:
                    continue
                matches.append(
                    {
                        "job_key": job_key,
                        "job_name": getattr(job, "name", None),
                        "job_path": getattr(job, "path", None),
                        "matching_tasks": matching_tasks,
                    }
                )
                if len(matches) == max_results:
                    break
            page = next_page(response)
            if not page:
                break
    return {
        "workspace_notebook_path": notebook_path,
        "jobs": matches,
        "is_truncated": bool(page) or len(matches) == max_results,
    }


def list_job_runs(settings, job_name=None, job_key=None, max_results=25):
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
    if (job_name is None) == (job_key is None):
        raise AidpError("Provide exactly one of job_name or job_key.")
    if job_name is not None and (not isinstance(job_name, str) or not job_name.strip()):
        raise AidpError("job_name must be a nonempty string.")
    if job_key is not None:
        validate_resource_key(job_key)
    if (
        isinstance(max_results, bool)
        or not isinstance(max_results, int)
        or not 1 <= max_results <= MAX_JOB_RUN_LIST_RESULTS
    ):
        raise AidpError("max_results must be an integer from 1 through 1000.")

    page = None
    runs = []
    with workspace_clients(settings) as clients:
        instance_id, workspace_key, _, _, workflows, _ = clients
        if job_name is not None:
            job = _find_job(workflows, instance_id, workspace_key, job_name)
            if job is None:
                raise AidpError(f"No visible job is named {job_name!r}.")
            selected_job_key = resource_key(job.data, "Job")
            selected_job_name = getattr(job.data, "name", job_name)
        else:
            selected_job_key = job_key
            selected_job_name = None

        while len(runs) < max_results:
            response = workflows.list_job_runs(
                instance_id,
                workspace_key,
                job_key=[selected_job_key],
                limit=min(25, max_results - len(runs)),
                page=page,
                sort_by="timeCreated",
                sort_order="DESC",
            )
            for item in getattr(response.data, "items", None) or []:
                runs.append(_run_response(item, resource_key(item, "Job run")))
                if len(runs) == max_results:
                    break
            page = next_page(response)
            if not page:
                break

    return {
        "job_key": selected_job_key,
        "job_name": selected_job_name,
        "job_runs": runs,
        "is_truncated": bool(page),
    }


def ensure_notebook_job(
    settings,
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
    if not job_name.replace("_", "").isalnum() or not job_name[0].isalpha():
        raise AidpError(
            "Job name must start with a letter and contain only letters, "
            "numbers, or underscores."
        )
    if not isinstance(max_concurrent_runs, int) or max_concurrent_runs < 1:
        raise AidpError("max_concurrent_runs must be a positive integer.")
    notebook_path = validate_workspace_path(workspace_notebook_path)
    if (
        not job_location.startswith("/Workspace/")
        or ".." in PurePosixPath(job_location).parts
    ):
        raise AidpError("Job location must be below /Workspace.")
    with workspace_clients(settings) as clients:
        instance_id, workspace_key, cluster_client, _, workflows, _ = clients
        target = find_cluster(cluster_client, instance_id, workspace_key, cluster_name)
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
        if not should_apply(apply, action):
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
            job_key = resource_key(created.data, "Job")
            workflows.update_job(
                instance_id,
                workspace_key,
                job_key,
                models.UpdateJobDetails(**details),
                retry_strategy=oci.retry.NoneRetryStrategy(),
            )
        else:
            job_key = resource_key(response.data, "Job")
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
    settings, job_name, *, wait=False, timeout_seconds=1200, confirm_start=False
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
    require_confirmation(
        confirm_start,
        "Set confirm_start=true to submit a compute-consuming job run.",
    )
    if not isinstance(timeout_seconds, int) or timeout_seconds < 1:
        raise AidpError("timeout_seconds must be a positive integer.")
    with workspace_clients(settings) as clients:
        instance_id, workspace_key, cluster_client, _, workflows, _ = clients
        response = _find_job(workflows, instance_id, workspace_key, job_name)
        if response is None or not _is_supported_job(response.data):
            raise AidpError("Job must be an existing managed single notebook task.")
        task = response.data.tasks[0]
        cluster_key = getattr(getattr(task, "cluster", None), "cluster_key", None)
        validate_resource_key(cluster_key)
        cluster = cluster_client.get_cluster(
            instance_id, workspace_key, cluster_key
        ).data
        if getattr(cluster, "state", None) != "ACTIVE":
            raise AidpError("The job cluster must be ACTIVE before submission.")
        job_key = resource_key(response.data, "Job")
        created = workflows.create_job_run(
            instance_id,
            workspace_key,
            models.CreateJobRunDetails(job_key=job_key, parameters=[]),
            retry_strategy=oci.retry.NoneRetryStrategy(),
        )
        run_key = resource_key(created.data, "Job run")
        result = {
            "job_name": job_name,
            "job_run_key": run_key,
            "state": "SUBMITTED",
        }
        if not wait:
            return result
        return _wait_for_run(
            workflows,
            instance_id=instance_id,
            workspace_key=workspace_key,
            run_key=run_key,
            timeout_seconds=timeout_seconds,
        )


def _wait_for_run(workflows, *, instance_id, workspace_key, run_key, timeout_seconds):
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


def get_job_run(settings, job_run_key):
    """Read a job run status without fetching logs or notebook output.

    Args:
        job_run_key: Existing AI DP job-run key.

    Returns:
        dict: Sanitized job-run status.
    """
    validate_resource_key(job_run_key)
    with workspace_clients(settings) as clients:
        instance_id, workspace_key, _, _, workflows, _ = clients
        response = workflows.get_job_run(instance_id, workspace_key, job_run_key)
        return _run_response(response.data, job_run_key)


def get_job_run_output(
    settings, job_run_key, max_characters=MAX_JOB_RUN_OUTPUT_CHARACTERS
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
    validate_resource_key(job_run_key)
    if (
        isinstance(max_characters, bool)
        or not isinstance(max_characters, int)
        or not 1 <= max_characters <= MAX_JOB_RUN_OUTPUT_CHARACTERS
    ):
        raise AidpError(
            "max_characters must be an integer from 1 to "
            f"{MAX_JOB_RUN_OUTPUT_CHARACTERS}."
        )
    with workspace_clients(settings) as clients:
        instance_id, workspace_key, _, _, workflows, _ = clients
        task_runs = oci.pagination.list_call_get_all_results(
            workflows.list_task_runs,
            instance_id,
            workspace_key,
            job_run_key,
            # AI DP rejects an unspecified sortBy value as null. Specify a
            # documented field so the SDK request is accepted consistently.
            sort_by="timeCreated",
        ).data
        if len(task_runs) != 1:
            raise AidpError("Job run must contain exactly one task run.")
        task_run = task_runs[0]
        task_run_key = resource_key(task_run, "Task run")
        output_key = getattr(task_run, "output_key", None)
        if not isinstance(output_key, str) or not output_key:
            raise AidpError("Task run has no available output key.")
        output = workflows.fetch_output(
            instance_id,
            workspace_key,
            task_run_key,
            models.FetchOutputDetails(output_key=output_key),
        ).data
        return _task_run_output_response(job_run_key, task_run, output, max_characters)


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
        instance_id, workspace_key, resource_key(matches[0], "Job")
    )


def _managed_task(notebook_path, cluster_key):
    cluster = models.JobCluster(cluster_key=cluster_key)
    return models.NotebookTask(
        task_key="notebook",
        notebook_path=notebook_path,
        source=models.NotebookTask.SOURCE_WORKSPACE,
        cluster=cluster,
        depends_on=[],
        run_if="ALL_SUCCESS",
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


def _matching_notebook_tasks(job, notebook_path):
    """Return sanitized workspace notebook tasks that use one notebook path."""
    matches = []
    for task in getattr(job, "tasks", None) or []:
        if (
            getattr(task, "type", None) != "NOTEBOOK_TASK"
            or getattr(task, "source", None) != "WORKSPACE"
            or _normalized_task_notebook_path(getattr(task, "notebook_path", None))
            != notebook_path
        ):
            continue
        cluster_key = getattr(getattr(task, "cluster", None), "cluster_key", None)
        matches.append(
            {
                "task_key": getattr(task, "task_key", None),
                "cluster_key": cluster_key if isinstance(cluster_key, str) else None,
            }
        )
    return matches


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


def _task_run_output_response(job_run_key, task_run, output, max_characters):
    """Filter and bound task output returned to the MCP client."""
    remaining = max_characters
    locally_truncated = False
    error_trace, remaining, error_trace_truncated = _take_text(
        getattr(output, "error_trace", None), remaining
    )
    locally_truncated = error_trace_truncated
    text_output = []
    for item in getattr(output, "data", None) or []:
        if (
            getattr(item, "type", None) != "TEXT_PLAIN"
            or getattr(item, "is_base64", False)
            or getattr(item, "compression", None)
        ):
            continue
        text, remaining, truncated = _take_text(getattr(item, "value", None), remaining)
        locally_truncated = locally_truncated or truncated
        if text is not None:
            text_output.append({"type": "TEXT_PLAIN", "text": text})
    return {
        "job_run_key": job_run_key,
        "task_run_key": resource_key(task_run, "Task run"),
        "task_key": getattr(task_run, "task_key", None),
        "output_key": getattr(output, "key", None),
        "task_type": getattr(output, "task_type", None),
        "text_output": text_output,
        "error_trace": error_trace,
        "is_truncated": bool(getattr(output, "is_truncated", False))
        or locally_truncated,
    }


def _take_text(value, remaining):
    """Return at most the remaining text characters and an exhaustion flag."""
    if not isinstance(value, str):
        return None, remaining, False
    if len(value) <= remaining:
        return value, remaining - len(value), False
    return value[:remaining], 0, True
