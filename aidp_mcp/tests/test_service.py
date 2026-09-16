"""
Author: L. Saetta
Date last modified: 2026-09-16
License: MIT
Description: Offline tests for AI DP MCP validation and tool registration.
"""

# pylint: disable=protected-access

import asyncio
from contextlib import contextmanager
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aidp_common.connection import AidpError
from aidp_mcp import service
from aidp_mcp.server import MCP


def test_validate_local_notebook_returns_json_and_digest(tmp_path, monkeypatch):
    """A repository-local valid notebook returns parsed content and a digest."""
    monkeypatch.setattr(service, "PROJECT_ROOT", tmp_path)
    notebook = tmp_path / "example.ipynb"
    notebook.write_text(json.dumps({"cells": [], "nbformat": 4}), encoding="utf-8")

    path, content, digest = service.validate_local_notebook(notebook)

    assert path == notebook
    assert content["nbformat"] == 4
    assert len(digest) == 64


def test_validate_local_notebook_rejects_path_outside_repository(tmp_path, monkeypatch):
    """The MCP adapter cannot upload arbitrary local paths."""
    monkeypatch.setattr(service, "PROJECT_ROOT", tmp_path / "repository")
    outside = tmp_path / "outside.ipynb"
    outside.write_text("{}", encoding="utf-8")

    with pytest.raises(AidpError, match="inside the repository"):
        service.validate_local_notebook(outside)


@pytest.mark.parametrize(
    "value",
    [
        "/Workspace/jobs/example.ipynb",
        "/Workspace/../unsafe.ipynb",
        "jobs/example.txt",
        "../unsafe.ipynb",
    ],
)
def test_validate_workspace_path_rejects_unsafe_values(value):
    """Only notebook paths below the workspace root are accepted."""
    with pytest.raises(AidpError):
        service.validate_workspace_path(value)


def test_validate_workspace_path_normalizes_valid_notebook_path():
    """A valid workspace-relative notebook path is retained."""
    assert service.validate_workspace_path("jobs/example.ipynb") == "jobs/example.ipynb"


@pytest.mark.parametrize("value", ["Workspace", "/Shared", "/Workspace/../unsafe", ""])
def test_validate_workspace_directory_rejects_unsafe_values(value):
    """Notebook listing is restricted to an absolute workspace directory."""
    with pytest.raises(AidpError):
        service.validate_workspace_directory(value)


def test_validate_workspace_directory_accepts_workspace_root():
    """The workspace root is an allowed read-only listing target."""
    assert service.validate_workspace_directory("/Workspace") == "/Workspace"


@pytest.mark.parametrize("value", ["notebooks/test00.ipynb", "/Workspace/jobs"])
def test_validate_workspace_notebook_path_rejects_nonabsolute_or_nonnotebook(value):
    """Job discovery accepts only one safe absolute notebook path."""
    with pytest.raises(AidpError):
        service.validate_workspace_notebook_path(value)


def test_normalized_task_notebook_path_accepts_relative_sdk_task_path():
    """Job task paths stored relative by the SDK match workspace discovery."""
    assert (
        service._normalized_task_notebook_path("notebooks/test00/test00.ipynb")
        == "/Workspace/notebooks/test00/test00.ipynb"
    )


def test_notebook_service_path_is_absolute_and_url_encoded():
    """Notebook API paths preserve the service root and encode path separators."""
    content_path = service.workspace_content_path("jobs/example.ipynb")

    assert content_path == "/Workspace/jobs/example.ipynb"
    assert (
        service.encoded_content_path(content_path)
        == "%2FWorkspace%2Fjobs%2Fexample.ipynb"
    )


def test_workspace_objects_request_uses_notebook_filter_and_page_token():
    """Listing calls the documented workspace-object endpoint with safe filters."""
    notebooks = SimpleNamespace(base_client=Mock())

    service.workspace_objects_request(
        notebooks,
        instance_id="instance",
        workspace_key="workspace",
        path="/Workspace",
        limit=25,
        page="next-page",
    )

    arguments = notebooks.base_client.call_api.call_args.kwargs
    assert arguments["method"] == "GET"
    assert arguments["query_params"] == {
        "path": "/Workspace",
        "type": "NOTEBOOK",
        "limit": 25,
        "page": "next-page",
    }
    assert arguments["response_type"] == "WorkspaceObjectCollection"


def test_internal_error_for_missing_content_is_a_create_plan():
    """AI DP returns this specific error form when a notebook is absent."""
    error = SimpleNamespace(
        status=500,
        code="InternalError",
        message="Unexpected error while getting notebook content",
    )

    assert service.is_missing_content_error(error)


def test_directory_conflict_is_a_retained_workspace_folder():
    """AI DP's known directory conflict permits an idempotent upload retry."""
    error = SimpleNamespace(
        status=409,
        code="Conflict",
        message="Directory already exists",
    )

    assert service.is_existing_folder_error(error)


def test_supported_job_requires_one_workspace_notebook_task():
    """Job reconciliation rejects unrelated or multi-task job definitions."""
    supported = SimpleNamespace(
        tasks=[
            SimpleNamespace(
                type="NOTEBOOK_TASK", source="WORKSPACE", task_key="notebook"
            )
        ]
    )
    unsupported = SimpleNamespace(tasks=[SimpleNamespace(type="PYTHON_TASK")])

    assert service._is_supported_job(supported)
    assert not service._is_supported_job(unsupported)


def test_managed_notebook_task_sets_required_all_success_run_condition():
    """The task builder supplies AI DP's required run condition."""
    task = service._managed_task("notebooks/test00/test00.ipynb", "cluster-key")

    assert task.run_if == "ALL_SUCCESS"


@pytest.mark.parametrize("job_name", ["test00-job", "_test00", "test00 job"])
def test_ensure_notebook_job_rejects_invalid_resource_names(job_name):
    """Invalid AI DP resource names fail before remote discovery."""
    workflow_service = service.AidpWorkflowService(settings=SimpleNamespace())

    with pytest.raises(AidpError, match="must start with a letter"):
        workflow_service.ensure_notebook_job(
            job_name,
            "notebooks/test00/test00.ipynb",
            "clu02",
        )


def test_mcp_workbench_clients_preserve_numeric_timestamps(monkeypatch):
    """All MCP AI DP clients retain numeric timestamps returned by the service."""
    settings = SimpleNamespace(
        endpoint=None, compartment="compartment", instance_id=None
    )
    managed = Mock(side_effect=[Mock() for _ in range(6)])

    monkeypatch.setattr(
        service, "load_auth", Mock(return_value=({"tenancy": "tenancy"}, {}))
    )
    monkeypatch.setattr(service, "managed_client", managed)
    monkeypatch.setattr(
        service, "resolve_compartment", Mock(return_value="compartment")
    )
    monkeypatch.setattr(service, "list_instances", Mock(return_value=[]))
    monkeypatch.setattr(service, "ExitStack", Mock(return_value=Mock()))

    with pytest.raises(AidpError, match="Exactly one active"):
        with service.AidpWorkflowService(settings)._clients():
            pass

    assert [
        call.kwargs.get("preserve_timestamps") for call in managed.call_args_list
    ] == [
        None,
        None,
        True,
        True,
        True,
        True,
    ]


def test_cluster_response_omits_sensitive_runtime_references():
    """Cluster status returns selected configuration rather than SDK internals."""
    cluster = SimpleNamespace(
        key="cluster-key",
        display_name="clu02",
        type="USER",
        state="ACTIVE",
        state_details="Ready",
        cluster_runtime_config=SimpleNamespace(runtime_version="3.5"),
        node_type="FLEX",
        driver_config=SimpleNamespace(
            driver_node_type="FLEX",
            driver_shape="VM.Standard.E5.Flex",
            driver_shape_config=SimpleNamespace(ocpus=2, memory_in_gbs=32),
        ),
        worker_config=SimpleNamespace(
            worker_shape="VM.Standard.E5.Flex",
            worker_shape_config=SimpleNamespace(ocpus=2, memory_in_gbs=32),
            min_worker_count=1,
            max_worker_count=3,
        ),
        auto_termination_minutes=30,
        jdbc_endpoint_url="sensitive-endpoint",
        log_group_id="sensitive-log-group",
    )

    result = service._cluster_response(cluster)

    assert result == {
        "cluster_key": "cluster-key",
        "display_name": "clu02",
        "type": "USER",
        "state": "ACTIVE",
        "state_details": "Ready",
        "runtime_version": "3.5",
        "node_type": "FLEX",
        "driver": {
            "node_type": "FLEX",
            "shape": "VM.Standard.E5.Flex",
            "ocpus": 2,
            "memory_in_gbs": 32,
        },
        "workers": {
            "shape": "VM.Standard.E5.Flex",
            "ocpus": 2,
            "memory_in_gbs": 32,
            "min_worker_count": 1,
            "max_worker_count": 3,
        },
        "auto_termination_minutes": 30,
    }


def test_set_cluster_state_starts_stopped_cluster_with_etag(monkeypatch):
    """Lifecycle start uses one ETag-guarded, no-retry SDK submission."""
    clusters = Mock()
    cluster = SimpleNamespace(key="cluster-key", display_name="clu02", state="STOPPED")
    workflow_service = service.AidpWorkflowService(settings=SimpleNamespace())

    @contextmanager
    def clients():
        yield "instance", "workspace", clusters, Mock(), Mock()

    monkeypatch.setattr(workflow_service, "_clients", clients)
    monkeypatch.setattr(
        service,
        "find_cluster_details",
        Mock(return_value=SimpleNamespace(data=cluster, headers={"etag": "etag"})),
    )
    clusters.start_cluster.return_value = SimpleNamespace(status=202)

    result = workflow_service.set_cluster_state("clu02", "start", confirm_action=True)

    assert result["outcome"] == "accepted"
    assert result["cluster"]["state"] == "STOPPED"
    arguments = clusters.start_cluster.call_args
    assert arguments.args[:3] == ("instance", "workspace", "cluster-key")
    assert type(arguments.args[3]).__name__ == "StartClusterDetails"
    assert arguments.kwargs["if_match"] == "etag"
    assert type(arguments.kwargs["retry_strategy"]).__name__ == "NoneRetryStrategy"
    assert arguments.kwargs["opc_retry_token"]


def test_set_cluster_state_requires_explicit_confirmation():
    """The lifecycle tool never performs discovery or mutation without consent."""
    workflow_service = service.AidpWorkflowService(settings=SimpleNamespace())

    with pytest.raises(AidpError, match="confirm_action=true"):
        workflow_service.set_cluster_state("clu02", "start")


def test_set_cluster_state_does_not_resubmit_active_start(monkeypatch):
    """An already active cluster is a successful lifecycle no-op."""
    clusters = Mock()
    cluster = SimpleNamespace(key="cluster-key", display_name="clu02", state="ACTIVE")
    workflow_service = service.AidpWorkflowService(settings=SimpleNamespace())

    @contextmanager
    def clients():
        yield "instance", "workspace", clusters, Mock(), Mock()

    monkeypatch.setattr(workflow_service, "_clients", clients)
    monkeypatch.setattr(
        service,
        "find_cluster_details",
        Mock(return_value=SimpleNamespace(data=cluster, headers={})),
    )

    result = workflow_service.set_cluster_state("clu02", "start", confirm_action=True)

    assert result["outcome"] == "already_desired"
    clusters.start_cluster.assert_not_called()


def test_set_cluster_state_stops_active_cluster(monkeypatch):
    """Lifecycle stop selects the typed stop request for an active cluster."""
    clusters = Mock()
    cluster = SimpleNamespace(key="cluster-key", display_name="clu02", state="ACTIVE")
    workflow_service = service.AidpWorkflowService(settings=SimpleNamespace())

    @contextmanager
    def clients():
        yield "instance", "workspace", clusters, Mock(), Mock()

    monkeypatch.setattr(workflow_service, "_clients", clients)
    monkeypatch.setattr(
        service,
        "find_cluster_details",
        Mock(return_value=SimpleNamespace(data=cluster, headers={})),
    )
    clusters.stop_cluster.return_value = SimpleNamespace(status=202)

    result = workflow_service.set_cluster_state("clu02", "stop", confirm_action=True)

    assert result["outcome"] == "accepted"
    assert (
        type(clusters.stop_cluster.call_args.args[3]).__name__ == "StopClusterDetails"
    )


@pytest.mark.parametrize("value", [0, True, "1200"])
def test_set_cluster_state_rejects_unsafe_wait_limits(value):
    """Cluster lifecycle polling bounds are validated before cloud discovery."""
    workflow_service = service.AidpWorkflowService(settings=SimpleNamespace())

    with pytest.raises(AidpError, match="timeout_seconds"):
        workflow_service.set_cluster_state(
            "clu02", "start", timeout_seconds=value, confirm_action=True
        )


def test_task_run_output_response_filters_and_bounds_output():
    """Only plain non-encoded text is returned within one character budget."""
    task_run = SimpleNamespace(key="task-run-key", task_key="notebook")
    output = SimpleNamespace(
        key="output-key",
        task_type="NOTEBOOK_TASK",
        is_truncated=False,
        error_trace="error",
        data=[
            SimpleNamespace(
                type="TEXT_PLAIN", is_base64=False, compression=None, value="abcdef"
            ),
            SimpleNamespace(
                type="TEXT_HTML", is_base64=False, compression=None, value="<secret>"
            ),
            SimpleNamespace(
                type="TEXT_PLAIN", is_base64=True, compression=None, value="encoded"
            ),
        ],
    )

    result = service._task_run_output_response("job-run-key", task_run, output, 7)

    assert result["error_trace"] == "error"
    assert result["text_output"] == [{"type": "TEXT_PLAIN", "text": "ab"}]
    assert result["is_truncated"] is True


def test_get_job_run_output_fetches_the_single_task_output(monkeypatch):
    """Output retrieval resolves one task run before calling the SDK fetch API."""
    workflows = Mock()
    task_run = SimpleNamespace(
        key="task-run-key", task_key="notebook", output_key="output"
    )
    workflows.fetch_output.return_value.data = SimpleNamespace(
        key="output",
        task_type="NOTEBOOK_TASK",
        is_truncated=False,
        error_trace=None,
        data=[],
    )
    workflow_service = service.AidpWorkflowService(settings=SimpleNamespace())

    @contextmanager
    def clients():
        yield "instance", "workspace", Mock(), Mock(), workflows

    monkeypatch.setattr(workflow_service, "_clients", clients)
    monkeypatch.setattr(
        service.oci.pagination,
        "list_call_get_all_results",
        Mock(return_value=SimpleNamespace(data=SimpleNamespace(items=[task_run]))),
    )

    result = workflow_service.get_job_run_output("job-run-key", 20)

    assert result["task_run_key"] == "task-run-key"
    assert workflows.fetch_output.call_args.args[:3] == (
        "instance",
        "workspace",
        "task-run-key",
    )
    assert workflows.fetch_output.call_args.args[3].output_key == "output"


def test_list_notebooks_paginates_and_filters_metadata(monkeypatch):
    """Listing returns bounded matching summaries without notebook content."""
    workflow_service = service.AidpWorkflowService(settings=SimpleNamespace())
    notebooks = Mock()
    first_page = SimpleNamespace(
        data=SimpleNamespace(
            items=[
                SimpleNamespace(
                    display_name="Other.ipynb",
                    path="/Workspace/Other.ipynb",
                    type="NOTEBOOK",
                    time_created="first",
                    time_updated="first-update",
                    content="sensitive",
                )
            ]
        ),
        headers={"opc-next-page": "next-page"},
    )
    second_page = SimpleNamespace(
        data=SimpleNamespace(
            items=[
                SimpleNamespace(
                    display_name="test00.ipynb",
                    path="/Workspace/test00.ipynb",
                    type="NOTEBOOK",
                    time_created="second",
                    time_updated="second-update",
                    content="sensitive",
                )
            ]
        ),
        headers={},
    )

    @contextmanager
    def clients():
        yield "instance", "workspace", Mock(), notebooks, Mock()

    monkeypatch.setattr(workflow_service, "_clients", clients)
    request = Mock(side_effect=[first_page, second_page])
    monkeypatch.setattr(service, "workspace_objects_request", request)

    result = workflow_service.list_notebooks(name_contains="TEST00", max_results=5)

    assert result == {
        "path": "/Workspace",
        "name_contains": "TEST00",
        "notebooks": [
            {
                "display_name": "test00.ipynb",
                "path": "/Workspace/test00.ipynb",
                "type": "NOTEBOOK",
                "time_created": "second",
                "time_updated": "second-update",
            }
        ],
        "is_truncated": False,
    }
    assert request.call_args_list[1].kwargs["page"] == "next-page"


def test_find_notebook_jobs_matches_workspace_tasks_and_paginates(monkeypatch):
    """Job discovery gets definitions and returns only matching task metadata."""
    workflow_service = service.AidpWorkflowService(settings=SimpleNamespace())
    workflows = Mock()
    first_page = SimpleNamespace(
        data=SimpleNamespace(items=[SimpleNamespace(key="unrelated")]),
        headers={"opc-next-page": "next-page"},
    )
    second_page = SimpleNamespace(
        data=SimpleNamespace(items=[SimpleNamespace(key="test00-job")]), headers={}
    )
    workflows.list_jobs.side_effect = [first_page, second_page]
    workflows.get_job.side_effect = [
        SimpleNamespace(
            data=SimpleNamespace(
                tasks=[
                    SimpleNamespace(
                        type="NOTEBOOK_TASK",
                        source="GIT_PROVIDER",
                        notebook_path="notebooks/test00/test00.ipynb",
                    )
                ]
            )
        ),
        SimpleNamespace(
            data=SimpleNamespace(
                name="test00_job",
                path="/Workspace/jobs",
                tasks=[
                    SimpleNamespace(
                        type="NOTEBOOK_TASK",
                        source="WORKSPACE",
                        task_key="notebook",
                        notebook_path="notebooks/test00/test00.ipynb",
                        cluster=SimpleNamespace(cluster_key="clu02-key"),
                    )
                ],
            )
        ),
    ]

    @contextmanager
    def clients():
        yield "instance", "workspace", Mock(), Mock(), workflows

    monkeypatch.setattr(workflow_service, "_clients", clients)

    result = workflow_service.find_notebook_jobs(
        "/Workspace/notebooks/test00/test00.ipynb", max_results=5
    )

    assert result == {
        "workspace_notebook_path": "/Workspace/notebooks/test00/test00.ipynb",
        "jobs": [
            {
                "job_key": "test00-job",
                "job_name": "test00_job",
                "job_path": "/Workspace/jobs",
                "matching_tasks": [
                    {"task_key": "notebook", "cluster_key": "clu02-key"}
                ],
            }
        ],
        "is_truncated": False,
    }
    assert workflows.list_jobs.call_args_list[1].kwargs["page"] == "next-page"


def test_find_notebook_jobs_reports_conservative_truncation(monkeypatch):
    """Hitting the requested match limit never claims an exhaustive search."""
    workflow_service = service.AidpWorkflowService(settings=SimpleNamespace())
    workflows = Mock()
    workflows.list_jobs.return_value = SimpleNamespace(
        data=SimpleNamespace(items=[SimpleNamespace(key="job-key")]), headers={}
    )
    workflows.get_job.return_value = SimpleNamespace(
        data=SimpleNamespace(
            name="job",
            path="/Workspace/jobs",
            tasks=[
                SimpleNamespace(
                    type="NOTEBOOK_TASK",
                    source="WORKSPACE",
                    task_key="notebook",
                    notebook_path="/Workspace/notebooks/test00/test00.ipynb",
                    cluster=SimpleNamespace(cluster_key="cluster-key"),
                )
            ],
        )
    )

    @contextmanager
    def clients():
        yield "instance", "workspace", Mock(), Mock(), workflows

    monkeypatch.setattr(workflow_service, "_clients", clients)

    result = workflow_service.find_notebook_jobs(
        "/Workspace/notebooks/test00/test00.ipynb", max_results=1
    )

    assert result["is_truncated"] is True


@pytest.mark.parametrize("value", [0, 1001, True, "100"])
def test_find_notebook_jobs_rejects_unsafe_result_limits(value):
    """Job discovery validates its result limit before cloud discovery."""
    workflow_service = service.AidpWorkflowService(settings=SimpleNamespace())

    with pytest.raises(AidpError, match="max_results"):
        workflow_service.find_notebook_jobs("/Workspace/example.ipynb", value)


@pytest.mark.parametrize("value", [0, 1001, True, "100"])
def test_list_notebooks_rejects_unsafe_result_limits(value):
    """Notebook listing validates its result limit before cloud discovery."""
    workflow_service = service.AidpWorkflowService(settings=SimpleNamespace())

    with pytest.raises(AidpError, match="max_results"):
        workflow_service.list_notebooks(max_results=value)


@pytest.mark.parametrize("value", [0, 12001, True, "12"])
def test_get_job_run_output_rejects_unsafe_character_limits(value):
    """The local output character bound is validated before cloud discovery."""
    workflow_service = service.AidpWorkflowService(settings=SimpleNamespace())

    with pytest.raises(AidpError, match="max_characters"):
        workflow_service.get_job_run_output("job-run-key", value)


def test_server_registers_the_nine_scoped_tools():
    """The MCP schema exposes the specified tools without cloud access."""
    names = {tool.name for tool in asyncio.run(MCP.list_tools())}
    assert names == {
        "upload_notebook",
        "list_notebooks",
        "find_notebook_jobs",
        "ensure_notebook_job",
        "start_notebook_job",
        "get_job_run",
        "get_cluster_status",
        "set_cluster_state",
        "get_job_run_output",
    }
