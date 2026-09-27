"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Offline tests for AI DP MCP managed notebook-job operations.
"""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aidp_common.connection import AidpError
from aidp_mcp import jobs


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

    assert getattr(jobs, "_is_supported_job")(supported)
    assert not getattr(jobs, "_is_supported_job")(unsupported)


def test_managed_notebook_task_sets_required_all_success_run_condition():
    """The task builder supplies AI DP's required run condition."""
    task = getattr(jobs, "_managed_task")(
        "notebooks/test00/test00.ipynb", "cluster-key"
    )

    assert task.run_if == "ALL_SUCCESS"


@pytest.mark.parametrize("job_name", ["test00-job", "_test00", "test00 job"])
def test_ensure_notebook_job_rejects_invalid_resource_names(job_name):
    """Invalid AI DP resource names fail before remote discovery."""
    settings = SimpleNamespace()

    with pytest.raises(AidpError, match="must start with a letter"):
        jobs.ensure_notebook_job(
            settings,
            job_name,
            "notebooks/test00/test00.ipynb",
            "clu02",
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

    result = getattr(jobs, "_task_run_output_response")(
        "job-run-key", task_run, output, 7
    )

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
    settings = SimpleNamespace()

    @contextmanager
    def clients():
        yield "instance", "workspace", Mock(), Mock(), workflows, Mock()

    monkeypatch.setattr(jobs, "workspace_clients", lambda _settings: clients())
    list_results = Mock(return_value=SimpleNamespace(data=[task_run]))
    monkeypatch.setattr(
        jobs.oci.pagination,
        "list_call_get_all_results",
        list_results,
    )

    result = jobs.get_job_run_output(settings, "job-run-key", 20)

    assert result["task_run_key"] == "task-run-key"
    assert workflows.fetch_output.call_args.args[:3] == (
        "instance",
        "workspace",
        "task-run-key",
    )
    assert workflows.fetch_output.call_args.args[3].output_key == "output"
    assert list_results.call_args.kwargs["sort_by"] == "timeCreated"


def test_find_notebook_jobs_matches_workspace_tasks_and_paginates(monkeypatch):
    """Job discovery gets definitions and returns only matching task metadata."""
    settings = SimpleNamespace()
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
        yield "instance", "workspace", Mock(), Mock(), workflows, Mock()

    monkeypatch.setattr(jobs, "workspace_clients", lambda _settings: clients())

    result = jobs.find_notebook_jobs(
        settings, "/Workspace/notebooks/test00/test00.ipynb", max_results=5
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
    settings = SimpleNamespace()
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
        yield "instance", "workspace", Mock(), Mock(), workflows, Mock()

    monkeypatch.setattr(jobs, "workspace_clients", lambda _settings: clients())

    result = jobs.find_notebook_jobs(
        settings, "/Workspace/notebooks/test00/test00.ipynb", max_results=1
    )

    assert result["is_truncated"] is True


def test_list_job_runs_resolves_name_and_paginates_newest_first(monkeypatch):
    """Run listing resolves a name and retains only bounded safe summaries."""
    settings = SimpleNamespace()
    workflows = Mock()
    monkeypatch.setattr(
        jobs,
        "_find_job",
        Mock(
            return_value=SimpleNamespace(
                data=SimpleNamespace(key="job-key", name="test00_job")
            )
        ),
    )
    workflows.list_job_runs.side_effect = [
        SimpleNamespace(
            data=SimpleNamespace(
                items=[
                    SimpleNamespace(
                        key="run-new",
                        state=SimpleNamespace(status="SUCCESS"),
                        start_time="start-new",
                        end_time="end-new",
                    )
                ]
            ),
            headers={"opc-next-page": "next-page"},
        ),
        SimpleNamespace(
            data=SimpleNamespace(
                items=[
                    SimpleNamespace(
                        key="run-old",
                        state=SimpleNamespace(status="FAILED"),
                        start_time="start-old",
                        end_time="end-old",
                    )
                ]
            ),
            headers={},
        ),
    ]

    @contextmanager
    def clients():
        yield "instance", "workspace", Mock(), Mock(), workflows, Mock()

    monkeypatch.setattr(jobs, "workspace_clients", lambda _settings: clients())

    result = jobs.list_job_runs(settings, job_name="test00_job", max_results=2)

    assert result == {
        "job_key": "job-key",
        "job_name": "test00_job",
        "job_runs": [
            {
                "job_run_key": "run-new",
                "state": "SUCCESS",
                "state_message": None,
                "start_time": "start-new",
                "end_time": "end-new",
            },
            {
                "job_run_key": "run-old",
                "state": "FAILED",
                "state_message": None,
                "start_time": "start-old",
                "end_time": "end-old",
            },
        ],
        "is_truncated": False,
    }
    assert workflows.list_job_runs.call_args_list[0].kwargs == {
        "job_key": ["job-key"],
        "limit": 2,
        "page": None,
        "sort_by": "timeCreated",
        "sort_order": "DESC",
    }
    assert workflows.list_job_runs.call_args_list[1].kwargs["page"] == "next-page"


def test_list_job_runs_accepts_a_key_without_job_discovery(monkeypatch):
    """A supplied key avoids an additional exact-name lookup."""
    settings = SimpleNamespace()
    workflows = Mock()
    workflows.list_job_runs.return_value = SimpleNamespace(
        data=SimpleNamespace(items=[]), headers={}
    )

    @contextmanager
    def clients():
        yield "instance", "workspace", Mock(), Mock(), workflows, Mock()

    monkeypatch.setattr(jobs, "workspace_clients", lambda _settings: clients())

    result = jobs.list_job_runs(settings, job_key="job-key")

    assert result == {
        "job_key": "job-key",
        "job_name": None,
        "job_runs": [],
        "is_truncated": False,
    }
    workflows.list_jobs.assert_not_called()


@pytest.mark.parametrize(
    ("job_name", "job_key"),
    [(None, None), ("test00_job", "job-key"), ("", None), (None, "bad/key")],
)
def test_list_job_runs_requires_one_safe_selector(job_name, job_key):
    """The list request does not allow ambiguous or unsafe job selectors."""
    settings = SimpleNamespace()

    with pytest.raises(AidpError):
        jobs.list_job_runs(settings, job_name=job_name, job_key=job_key)


@pytest.mark.parametrize("value", [0, 1001, True, "100"])
def test_list_job_runs_rejects_unsafe_result_limits(value):
    """Job-run listing bounds API pagination before cloud discovery."""
    settings = SimpleNamespace()

    with pytest.raises(AidpError, match="max_results"):
        jobs.list_job_runs(settings, job_key="job-key", max_results=value)


@pytest.mark.parametrize("value", [0, 1001, True, "100"])
def test_find_notebook_jobs_rejects_unsafe_result_limits(value):
    """Job discovery validates its result limit before cloud discovery."""
    settings = SimpleNamespace()

    with pytest.raises(AidpError, match="max_results"):
        jobs.find_notebook_jobs(settings, "/Workspace/example.ipynb", value)


@pytest.mark.parametrize("value", [0, 12001, True, "12"])
def test_get_job_run_output_rejects_unsafe_character_limits(value):
    """The local output character bound is validated before cloud discovery."""
    settings = SimpleNamespace()

    with pytest.raises(AidpError, match="max_characters"):
        jobs.get_job_run_output(settings, "job-run-key", value)
