"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Offline tests for AI DP MCP validation and tool registration.
"""

# pylint: disable=protected-access,too-many-lines

import asyncio
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import Mock

import oci
import pytest
from fastmcp import Client

from aidp_common.connection import AidpError
from aidp_common import settings as common_settings
from aidp_mcp import service
from aidp_mcp import server
from aidp_mcp.server import MCP


@pytest.fixture(autouse=True)
def clear_process_target_cache():
    """Keep module-level target resolution state out of unrelated tests."""
    service.clear_target_cache()
    yield
    service.clear_target_cache()


def test_validate_local_notebook_returns_json_and_digest(tmp_path, monkeypatch):
    """A repository-local valid notebook returns parsed content and a digest."""
    monkeypatch.setattr(service, "PROJECT_ROOT", tmp_path)
    notebook = tmp_path / "example.ipynb"
    notebook.write_text(json.dumps({"cells": [], "nbformat": 4}), encoding="utf-8")

    path, root, content, digest = service.validate_local_notebook(notebook)

    assert path == notebook
    assert root == tmp_path
    assert content["nbformat"] == 4
    assert len(digest) == 64


def test_validate_local_notebook_rejects_path_outside_repository(tmp_path, monkeypatch):
    """The MCP adapter cannot upload arbitrary local paths."""
    monkeypatch.setattr(service, "PROJECT_ROOT", tmp_path / "repository")
    outside = tmp_path / "outside.ipynb"
    outside.write_text("{}", encoding="utf-8")

    with pytest.raises(AidpError, match="AIDP_ALLOWED_ROOTS"):
        service.validate_local_notebook(outside)


def test_allowed_roots_default_to_project_root(tmp_path, monkeypatch):
    """An absent allowed-roots setting retains the repository-only boundary."""
    monkeypatch.setattr(service, "PROJECT_ROOT", tmp_path)

    assert service.allowed_local_roots("") == (tmp_path,)


def test_validate_local_notebook_accepts_second_configured_root(tmp_path):
    """A notebook under any configured root is accepted with its matching root."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    notebook = second / "nested" / "example.ipynb"
    notebook.parent.mkdir()
    notebook.write_text('{"nbformat": 4}', encoding="utf-8")
    roots = service.allowed_local_roots(f"{first}{service.os.pathsep}{second}")

    path, matching_root, content, _ = service.validate_local_notebook(notebook, roots)

    assert path == notebook
    assert content == {"nbformat": 4}
    assert matching_root == second


def test_validate_local_notebook_rejects_ambiguous_relative_path(tmp_path, monkeypatch):
    """Two roots cannot silently select the server-root copy of one notebook."""
    server_root = tmp_path / "server-repository"
    extra_root = tmp_path / "other-project"
    relative_path = "notebooks/example.ipynb"
    for root in (server_root, extra_root):
        notebook = root / relative_path
        notebook.parent.mkdir(parents=True)
        notebook.write_text('{"nbformat": 4}', encoding="utf-8")
    monkeypatch.chdir(server_root)

    with pytest.raises(AidpError, match="must be absolute"):
        service.validate_local_notebook(relative_path, (server_root, extra_root))


def test_validate_local_notebook_rejects_relative_path_with_one_external_root(
    tmp_path, monkeypatch
):
    """An explicitly configured non-repository root cannot accept a relative path."""
    repository_root = tmp_path / "server-repository"
    external_root = tmp_path / "other-project"
    repository_root.mkdir()
    external_root.mkdir()
    monkeypatch.chdir(repository_root)
    monkeypatch.setattr(service, "PROJECT_ROOT", repository_root)

    with pytest.raises(AidpError, match="must be absolute"):
        service.validate_local_notebook("notebooks/example.ipynb", (external_root,))


def test_validate_local_notebook_accepts_relative_path_with_default_root(
    tmp_path, monkeypatch
):
    """The default repository root preserves its documented relative-path mode."""
    notebook = tmp_path / "notebooks" / "example.ipynb"
    notebook.parent.mkdir()
    notebook.write_text('{"nbformat": 4}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(service, "PROJECT_ROOT", tmp_path)

    path, root, _, _ = service.validate_local_notebook("notebooks/example.ipynb")

    assert path == notebook
    assert root == tmp_path


def test_validate_local_path_rejects_symlink_that_escapes_allowed_root(tmp_path):
    """Path resolution prevents an allowed-root symlink from exposing another file."""
    root = tmp_path / "allowed"
    root.mkdir()
    outside = tmp_path / "outside.ipynb"
    outside.write_text("{}", encoding="utf-8")
    linked = root / "linked.ipynb"
    linked.symlink_to(outside)

    with pytest.raises(AidpError, match="AIDP_ALLOWED_ROOTS"):
        service.validate_local_notebook(linked, (root,))


@pytest.mark.parametrize("kind", ["filesystem", "home", "ancestor", "missing", "file"])
def test_allowed_roots_rejects_broad_or_invalid_directories(
    tmp_path, monkeypatch, kind
):
    """Invalid root settings do not reveal their configured local paths."""
    sandbox = tmp_path / "sandbox"
    home = sandbox / "home"
    home.mkdir(parents=True)
    regular_file = sandbox / "not-directory"
    regular_file.write_text("data", encoding="utf-8")
    monkeypatch.setattr(service.Path, "home", classmethod(lambda _cls: home))
    values = {
        "filesystem": "/",
        "home": str(home),
        "ancestor": str(sandbox),
        "missing": str(sandbox / "missing"),
        "file": str(regular_file),
    }

    with pytest.raises(AidpError) as error:
        service.allowed_local_roots(values[kind])

    assert "AIDP_ALLOWED_ROOTS" in str(error.value)
    assert values[kind] not in str(error.value)


def _mcp_settings_from_env(monkeypatch, env_file):
    """Load MCP settings from one test-only dotenv file."""
    monkeypatch.setattr(
        service,
        "connection_parser",
        lambda argv, description, parser_class: common_settings.connection_parser(
            argv, description, env_file, parser_class
        ),
    )
    return service.load_connection_settings()


def test_load_connection_settings_converts_missing_workspace_to_aidp_error(
    tmp_path, monkeypatch
):
    """Incomplete MCP settings return an error instead of ending the server."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "COMPARTMENT=compartment\nOCI_CONFIG_FILE=config\nOCI_PROFILE=DEFAULT\n"
        "REGION=eu-frankfurt-1\n",
        encoding="utf-8",
    )

    with pytest.raises(AidpError, match="WORKSPACE_NAME"):
        _mcp_settings_from_env(monkeypatch, env_file)


def test_load_connection_settings_converts_invalid_region_to_aidp_error(
    tmp_path, monkeypatch
):
    """Argparse region validation remains actionable without SystemExit."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "COMPARTMENT=compartment\nOCI_CONFIG_FILE=config\nOCI_PROFILE=DEFAULT\n"
        "REGION=not-a-region\nWORKSPACE_NAME=workspace\n",
        encoding="utf-8",
    )

    with pytest.raises(AidpError, match="REGION"):
        _mcp_settings_from_env(monkeypatch, env_file)


def test_mcp_parser_converts_missing_explicit_env_file_to_aidp_error(tmp_path):
    """Parser setup errors remain tool errors when the MCP parser is selected."""
    missing = tmp_path / "missing.env"

    with pytest.raises(AidpError, match="selected by --env-file"):
        common_settings.connection_parser(
            ["--env-file", str(missing)],
            "Test MCP settings.",
            parser_class=service.McpArgumentParser,
        )


def test_mcp_settings_file_error_hides_path_and_keeps_session_alive(
    tmp_path, monkeypatch
):
    """An absent process-selected dotenv remains one nonfatal MCP tool error."""
    missing = tmp_path / "missing.env"
    monkeypatch.setenv("AIDP_ENV_FILE", str(missing))
    healthy_service = Mock()
    healthy_service.list_notebooks.return_value = {"notebooks": []}
    first_call = True

    def create_service():
        nonlocal first_call
        if first_call:
            first_call = False
            return service.AidpWorkflowService()
        return healthy_service

    monkeypatch.setattr(server, "_service", create_service)

    async def call_tools():
        async with Client(MCP) as client:
            invalid = await client.call_tool("list_notebooks", raise_on_error=False)
            healthy = await client.call_tool("list_notebooks", raise_on_error=False)
        return invalid, healthy

    invalid_result, healthy_result = asyncio.run(call_tools())

    assert invalid_result.is_error
    assert "AIDP_ENV_FILE" in invalid_result.content[0].text
    assert str(missing) not in invalid_result.content[0].text
    assert not healthy_result.is_error


def test_mcp_selected_settings_without_workspace_keeps_session_alive(
    tmp_path, monkeypatch
):
    """A selected dotenv missing WORKSPACE_NAME produces one recoverable error."""
    settings_file = tmp_path / "selected.env"
    settings_file.write_text(
        "COMPARTMENT=compartment\nOCI_CONFIG_FILE=config\nOCI_PROFILE=DEFAULT\n"
        "REGION=eu-frankfurt-1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AIDP_ENV_FILE", str(settings_file))
    healthy_service = Mock()
    healthy_service.list_notebooks.return_value = {"notebooks": []}
    first_call = True

    def create_service():
        nonlocal first_call
        if first_call:
            first_call = False
            return service.AidpWorkflowService()
        return healthy_service

    monkeypatch.setattr(server, "_service", create_service)

    async def call_tools():
        async with Client(MCP) as client:
            invalid = await client.call_tool("list_notebooks", raise_on_error=False)
            healthy = await client.call_tool("list_notebooks", raise_on_error=False)
        return invalid, healthy

    invalid_result, healthy_result = asyncio.run(call_tools())

    assert invalid_result.is_error
    assert "WORKSPACE_NAME" in invalid_result.content[0].text
    assert not healthy_result.is_error


def test_mcp_missing_default_settings_names_root_env_and_selector(
    tmp_path, monkeypatch
):
    """No available MCP settings tells the operator both supported locations."""
    missing_default = tmp_path / "missing-default.env"
    monkeypatch.delenv("AIDP_ENV_FILE", raising=False)
    monkeypatch.setattr(
        service,
        "connection_parser",
        lambda argv, description, parser_class: common_settings.connection_parser(
            argv, description, missing_default, parser_class
        ),
    )

    with pytest.raises(AidpError) as error:
        service.load_connection_settings()

    assert "root .env" in str(error.value)
    assert "AIDP_ENV_FILE" in str(error.value)


def test_server_main_is_callable_without_arguments():
    """The console-script target imports without entering the stdio loop."""
    assert callable(server.main)


def test_process_allowed_roots_overrides_dotenv_value(tmp_path, monkeypatch):
    """Process configuration keeps its documented precedence over dotenv."""
    dotenv_root = tmp_path / "dotenv-root"
    environment_root = tmp_path / "environment-root"
    dotenv_root.mkdir()
    environment_root.mkdir()
    env_file = tmp_path / ".env"
    env_file.write_text(
        "COMPARTMENT=compartment\nOCI_CONFIG_FILE=config\nOCI_PROFILE=DEFAULT\n"
        "REGION=eu-frankfurt-1\nWORKSPACE_NAME=workspace\n"
        f"AIDP_ALLOWED_ROOTS={dotenv_root}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AIDP_ALLOWED_ROOTS", str(environment_root))

    loaded = _mcp_settings_from_env(monkeypatch, env_file)

    assert loaded.allowed_roots == (environment_root.resolve(),)


def test_upload_plan_uses_matching_extra_root_without_remote_write(
    tmp_path, monkeypatch
):
    """A plan from a second root exposes only relative local location details."""
    root = tmp_path / "my-langgraph-agent"
    root.mkdir()
    notebook = root / "notebooks" / "example.ipynb"
    notebook.parent.mkdir()
    notebook.write_text('{"nbformat": 4}', encoding="utf-8")
    workflow_service = service.AidpWorkflowService(
        settings=SimpleNamespace(allowed_roots=(root,))
    )

    @contextmanager
    def clients():
        yield "instance", "workspace", Mock(), Mock(), Mock()

    missing = oci.exceptions.ServiceError(404, "NotFound", {}, "missing")
    request = Mock(side_effect=missing)
    monkeypatch.setattr(workflow_service, "_clients", clients)
    monkeypatch.setattr(service, "notebook_content_request", request)

    result = workflow_service.upload_notebook(
        notebook, "plans/example.ipynb", apply=False
    )

    assert result == {
        "action": "create",
        "apply": False,
        "local_path": "notebooks/example.ipynb",
        "local_root": "my-langgraph-agent",
        "workspace_path": "plans/example.ipynb",
        "sha256": result["sha256"],
    }
    assert len(result["sha256"]) == 64
    assert request.call_args.kwargs["method"] == "GET"


def test_upload_notebook_validates_the_local_path_once(tmp_path, monkeypatch):
    """Upload reuses the matching root returned by notebook validation."""
    root = tmp_path / "allowed"
    root.mkdir()
    notebook = root / "example.ipynb"
    notebook.write_text('{"nbformat": 4}', encoding="utf-8")
    workflow_service = service.AidpWorkflowService(
        settings=SimpleNamespace(allowed_roots=(root,))
    )
    original = service.validate_local_path
    path_validation = Mock(wraps=original)
    missing = oci.exceptions.ServiceError(404, "NotFound", {}, "missing")

    @contextmanager
    def clients():
        yield "instance", "workspace", Mock(), Mock(), Mock()

    monkeypatch.setattr(workflow_service, "_clients", clients)
    monkeypatch.setattr(service, "validate_local_path", path_validation)
    monkeypatch.setattr(service, "notebook_content_request", Mock(side_effect=missing))

    workflow_service.upload_notebook(notebook, "plans/example.ipynb", apply=False)

    assert path_validation.call_count == 1


def test_mcp_session_survives_a_configuration_error(monkeypatch):
    """A tool error from invalid settings does not close an in-memory session."""
    healthy_service = Mock()
    healthy_service.list_notebooks.return_value = {"notebooks": []}
    create_service = Mock(
        side_effect=[AidpError("Set WORKSPACE_NAME first."), healthy_service]
    )
    monkeypatch.setattr(server, "_service", create_service)

    async def call_tools():
        async with Client(MCP) as client:
            invalid = await client.call_tool("list_notebooks", raise_on_error=False)
            healthy = await client.call_tool("list_notebooks", raise_on_error=False)
        return invalid, healthy

    invalid_result, healthy_result = asyncio.run(call_tools())

    assert invalid_result.is_error
    assert "WORKSPACE_NAME" in invalid_result.content[0].text
    assert not healthy_result.is_error
    healthy_service.list_notebooks.assert_called_once_with("/Workspace", None, 100)


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
    ] == [None, None]


def _target_settings(**overrides):
    """Return complete target-selection settings for resolution tests."""
    values = {
        "config_file": "~/.oci/config",
        "profile": "DEFAULT",
        "region": "eu-frankfurt-1",
        "compartment": "ocid1.compartment.example",
        "instance_id": "ocid1.aidp.example",
        "workspace_name": "workspace",
        "endpoint": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _resolve_cached_target(settings, monkeypatch, *, need_workspace):
    """Resolve through the cache with discovery replaced by offline mocks."""
    discover = Mock(return_value=[SimpleNamespace(id="instance")])
    workspace = Mock(return_value="workspace-key")
    monkeypatch.setattr(service, "_discover_instances", discover)
    monkeypatch.setattr(service, "find_workspace", workspace)
    managed = Mock(return_value=Mock())
    monkeypatch.setattr(service, "managed_client", managed)
    result = service._resolve_target(
        settings,
        {"tenancy": "tenancy"},
        {},
        {},
        Mock(),
        need_workspace=need_workspace,
    )
    return result, discover, workspace, managed


def test_target_resolution_reuses_complete_workspace_target(monkeypatch):
    """An identical second workspace resolution performs no discovery or setup."""
    settings = _target_settings()
    first, discover, workspace, managed = _resolve_cached_target(
        settings, monkeypatch, need_workspace=True
    )
    second = service._resolve_target(
        settings,
        {"tenancy": "tenancy"},
        {},
        {},
        Mock(),
        need_workspace=True,
    )

    assert first[0] == service.ResolvedTarget("instance", "workspace-key")
    assert first[2] is False
    assert second[0] == first[0]
    assert second[2] is True
    discover.assert_called_once()
    workspace.assert_called_once()
    assert managed.call_count == 3


def test_catalog_then_workspace_reuses_cached_instance(monkeypatch):
    """Adding a workspace key does not rediscover the selected instance."""
    settings = _target_settings()
    _, discover, workspace, _ = _resolve_cached_target(
        settings, monkeypatch, need_workspace=False
    )
    service._resolve_target(
        settings,
        {"tenancy": "tenancy"},
        {},
        {},
        Mock(),
        need_workspace=True,
    )

    discover.assert_called_once()
    workspace.assert_called_once()


def test_target_resolution_uses_new_key_when_workspace_changes(monkeypatch):
    """A target setting change cannot reuse identifiers from another target."""
    settings = _target_settings()
    _, discover, _, _ = _resolve_cached_target(
        settings, monkeypatch, need_workspace=False
    )
    service._resolve_target(
        _target_settings(workspace_name="other-workspace"),
        {"tenancy": "tenancy"},
        {},
        {},
        Mock(),
        need_workspace=False,
    )

    assert discover.call_count == 2


def test_target_resolution_concurrent_misses_discover_once(monkeypatch):
    """The process lock coalesces two simultaneous first resolutions."""
    settings = _target_settings()

    def discover(*_args):
        time.sleep(0.05)
        return [SimpleNamespace(id="instance")]

    mocked_discover = Mock(side_effect=discover)
    monkeypatch.setattr(service, "_discover_instances", mocked_discover)
    monkeypatch.setattr(service, "managed_client", Mock(return_value=Mock()))

    def resolve():
        return service._resolve_target(
            settings,
            {"tenancy": "tenancy"},
            {},
            {},
            Mock(),
            need_workspace=False,
        )[0]

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _unused: resolve(), range(2)))

    assert results == [
        service.ResolvedTarget("instance"),
        service.ResolvedTarget("instance"),
    ]
    mocked_discover.assert_called_once()


def test_target_resolution_failure_does_not_populate_cache(monkeypatch):
    """A failed discovery cannot leave a partial target for a later call."""
    settings = _target_settings()
    monkeypatch.setattr(
        service, "_discover_instances", Mock(side_effect=AidpError("no"))
    )
    monkeypatch.setattr(service, "managed_client", Mock(return_value=Mock()))

    with pytest.raises(AidpError, match="no"):
        service._resolve_target(
            settings,
            {"tenancy": "tenancy"},
            {},
            {},
            Mock(),
            need_workspace=False,
        )

    assert not service._TARGET_CACHE


def test_cached_target_is_cleared_after_tool_404(monkeypatch):
    """A 404 from tool work invalidates the complete cached target once."""
    settings = _target_settings()
    cache_key = ("target",)
    service._TARGET_CACHE[cache_key] = service.ResolvedTarget(
        "instance", "workspace-key"
    )
    monkeypatch.setattr(
        service,
        "load_auth",
        Mock(return_value=({"tenancy": "tenancy"}, {})),
    )
    monkeypatch.setattr(
        service,
        "_resolve_target",
        Mock(
            return_value=(
                service.ResolvedTarget("instance", "workspace-key"),
                cache_key,
                True,
            )
        ),
    )
    monkeypatch.setattr(service, "managed_client", Mock(return_value=Mock()))

    with pytest.raises(oci.exceptions.ServiceError):
        with service.AidpWorkflowService(settings)._clients():
            raise oci.exceptions.ServiceError(404, "NotFound", {}, "gone")

    assert cache_key not in service._TARGET_CACHE


def test_named_compartment_uses_instance_compartment_lookup():
    """An explicit instance avoids tenancy-wide compartment enumeration."""
    identity = Mock()
    control = Mock()
    instance = SimpleNamespace(
        id="instance", compartment_id="compartment-id", lifecycle_state="ACTIVE"
    )
    control.get_ai_data_platform.return_value.data = instance
    identity.get_compartment.return_value.data = SimpleNamespace(
        name="development", lifecycle_state="ACTIVE"
    )

    result = service._discover_instances(
        identity,
        control,
        {"tenancy": "tenancy"},
        _target_settings(compartment="development"),
    )

    assert result == [instance]
    control.get_ai_data_platform.assert_called_once_with("ocid1.aidp.example")
    identity.get_compartment.assert_called_once_with("compartment-id")


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
    list_results = Mock(return_value=SimpleNamespace(data=[task_run]))
    monkeypatch.setattr(
        service.oci.pagination,
        "list_call_get_all_results",
        list_results,
    )

    result = workflow_service.get_job_run_output("job-run-key", 20)

    assert result["task_run_key"] == "task-run-key"
    assert workflows.fetch_output.call_args.args[:3] == (
        "instance",
        "workspace",
        "task-run-key",
    )
    assert workflows.fetch_output.call_args.args[3].output_key == "output"
    assert list_results.call_args.kwargs["sort_by"] == "timeCreated"


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


def test_list_job_runs_resolves_name_and_paginates_newest_first(monkeypatch):
    """Run listing resolves a name and retains only bounded safe summaries."""
    workflow_service = service.AidpWorkflowService(settings=SimpleNamespace())
    workflows = Mock()
    monkeypatch.setattr(
        service,
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
        yield "instance", "workspace", Mock(), Mock(), workflows

    monkeypatch.setattr(workflow_service, "_clients", clients)

    result = workflow_service.list_job_runs(job_name="test00_job", max_results=2)

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
    workflow_service = service.AidpWorkflowService(settings=SimpleNamespace())
    workflows = Mock()
    workflows.list_job_runs.return_value = SimpleNamespace(
        data=SimpleNamespace(items=[]), headers={}
    )

    @contextmanager
    def clients():
        yield "instance", "workspace", Mock(), Mock(), workflows

    monkeypatch.setattr(workflow_service, "_clients", clients)

    result = workflow_service.list_job_runs(job_key="job-key")

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
    workflow_service = service.AidpWorkflowService(settings=SimpleNamespace())

    with pytest.raises(AidpError):
        workflow_service.list_job_runs(job_name=job_name, job_key=job_key)


@pytest.mark.parametrize("value", [0, 1001, True, "100"])
def test_list_job_runs_rejects_unsafe_result_limits(value):
    """Job-run listing bounds API pagination before cloud discovery."""
    workflow_service = service.AidpWorkflowService(settings=SimpleNamespace())

    with pytest.raises(AidpError, match="max_results"):
        workflow_service.list_job_runs(job_key="job-key", max_results=value)


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


def test_list_catalog_volumes_returns_only_external_metadata(monkeypatch):
    """External-volume discovery resolves the hierarchy and omits managed data."""
    workflow_service = service.AidpWorkflowService(settings=SimpleNamespace())
    catalogs, schemas, volumes = Mock(), Mock(), Mock()
    catalog = SimpleNamespace(key="catalog-key", display_name="catalog")
    schema_a = SimpleNamespace(key="catalog.a", display_name="a")
    schema_b = SimpleNamespace(key="catalog.b", display_name="b")
    managed = SimpleNamespace(key="managed-key", display_name="managed")
    external_a = SimpleNamespace(key="external-a-key", display_name="external-a")
    external_b = SimpleNamespace(key="external-b-key", display_name="external-b")

    @contextmanager
    def catalog_clients():
        yield "instance", catalogs, schemas, volumes

    monkeypatch.setattr(workflow_service, "_catalog_clients", catalog_clients)
    monkeypatch.setattr(
        service.oci.pagination,
        "list_call_get_all_results",
        Mock(
            side_effect=[
                SimpleNamespace(data=[catalog]),
                SimpleNamespace(data=[schema_b, schema_a]),
                SimpleNamespace(data=[managed, external_a]),
                SimpleNamespace(data=[external_b]),
            ]
        ),
    )
    volumes.get_volume.side_effect = [
        SimpleNamespace(
            data=SimpleNamespace(
                key="managed-key",
                display_name="managed",
                volume_type="MANAGED",
                full_name="catalog.a.managed",
                lifecycle_state="ACTIVE",
            )
        ),
        SimpleNamespace(
            data=SimpleNamespace(
                key="external-a-key",
                display_name="external-a",
                volume_type="EXTERNAL",
                full_name="catalog.a.external-a",
                storage_location="oci://bucket@namespace/a/",
                lifecycle_state="ACTIVE",
            )
        ),
        SimpleNamespace(
            data=SimpleNamespace(
                key="external-b-key",
                display_name="external-b",
                volume_type="EXTERNAL",
                full_name="catalog.b.external-b",
                storage_location="oci://bucket@namespace/b/",
                lifecycle_state="ACTIVE",
            )
        ),
    ]

    result = workflow_service.list_catalog_volumes("catalog")

    assert result == {
        "catalog_name": "catalog",
        "external_only": True,
        "schemas": [
            {
                "display_name": "a",
                "volumes": [
                    {
                        "display_name": "external-a",
                        "volume_key": "external-a-key",
                        "full_name": "catalog.a.external-a",
                        "volume_type": "EXTERNAL",
                        "storage_location": "oci://bucket@namespace/a/",
                        "lifecycle_state": "ACTIVE",
                    }
                ],
            },
            {
                "display_name": "b",
                "volumes": [
                    {
                        "display_name": "external-b",
                        "volume_key": "external-b-key",
                        "full_name": "catalog.b.external-b",
                        "volume_type": "EXTERNAL",
                        "storage_location": "oci://bucket@namespace/b/",
                        "lifecycle_state": "ACTIVE",
                    }
                ],
            },
        ],
        "is_truncated": False,
    }


def test_list_volume_files_builds_a_sanitized_recursive_tree(monkeypatch):
    """File browsing returns hierarchy metadata without arbitrary SDK fields."""
    workflow_service = service.AidpWorkflowService(settings=SimpleNamespace())
    catalogs, schemas, volumes = Mock(), Mock(), Mock()
    catalog = SimpleNamespace(key="catalog-key", display_name="catalog")
    schema = SimpleNamespace(key="catalog.schema", display_name="schema")
    volume = SimpleNamespace(key="volume-key", display_name="volume")

    @contextmanager
    def catalog_clients():
        yield "instance", catalogs, schemas, volumes

    monkeypatch.setattr(workflow_service, "_catalog_clients", catalog_clients)
    monkeypatch.setattr(
        service.oci.pagination,
        "list_call_get_all_results",
        Mock(
            side_effect=[
                SimpleNamespace(data=[catalog]),
                SimpleNamespace(data=[schema]),
                SimpleNamespace(data=[volume]),
            ]
        ),
    )
    volumes.list_files.return_value = SimpleNamespace(
        data=SimpleNamespace(
            items=[
                SimpleNamespace(
                    display_name="reports",
                    path="/Volumes/catalog/schema/volume/reports",
                    type="FOLDER",
                    time_created="folder-created",
                    time_updated="folder-updated",
                    metadata={"sensitive": "metadata"},
                ),
                SimpleNamespace(
                    display_name="summary.csv",
                    path="/Volumes/catalog/schema/volume/reports/2026/summary.csv",
                    type="FILE",
                    time_created="file-created",
                    time_updated="file-updated",
                    description="sensitive description",
                ),
            ]
        ),
        headers={},
    )

    result = workflow_service.list_volume_files("catalog", "schema", "volume")

    assert result["volume"] == {
        "catalog_name": "catalog",
        "schema_name": "schema",
        "display_name": "volume",
        "volume_key": "volume-key",
    }
    reports = result["root"]["children"][0]
    assert reports["display_name"] == "reports"
    assert reports["type"] == "FOLDER"
    inferred = reports["children"][0]
    assert inferred["inferred"] is True
    assert inferred["children"][0] == {
        "display_name": "summary.csv",
        "path": "/reports/2026/summary.csv",
        "type": "FILE",
        "time_created": "file-created",
        "time_updated": "file-updated",
    }
    assert result["is_truncated"] is False
    arguments = volumes.list_files.call_args
    assert arguments.args == (
        "instance",
        "volume-key",
        "/",
    )
    assert arguments.kwargs["is_recursive"] is True


def test_list_volume_files_keeps_a_logical_child_path_in_the_request(monkeypatch):
    """The SDK receives the public path while mount-prefixed responses normalize."""
    workflow_service = service.AidpWorkflowService(settings=SimpleNamespace())
    catalogs, schemas, volumes = Mock(), Mock(), Mock()
    catalog = SimpleNamespace(key="catalog-key", display_name="catalog")
    schema = SimpleNamespace(key="catalog.schema", display_name="schema")
    volume = SimpleNamespace(key="volume-key", display_name="volume")

    @contextmanager
    def catalog_clients():
        yield "instance", catalogs, schemas, volumes

    monkeypatch.setattr(workflow_service, "_catalog_clients", catalog_clients)
    monkeypatch.setattr(
        service.oci.pagination,
        "list_call_get_all_results",
        Mock(
            side_effect=[
                SimpleNamespace(data=[catalog]),
                SimpleNamespace(data=[schema]),
                SimpleNamespace(data=[volume]),
            ]
        ),
    )
    volumes.list_files.return_value = SimpleNamespace(
        data=SimpleNamespace(
            items=[
                SimpleNamespace(
                    display_name="train.jsonl",
                    path="/Volumes/catalog/schema/volume/datasets/train.jsonl",
                    type="FILE",
                    time_created="created",
                    time_updated="updated",
                )
            ]
        ),
        headers={},
    )

    result = workflow_service.list_volume_files(
        "catalog", "schema", "volume", path="/datasets"
    )

    assert volumes.list_files.call_args.args == (
        "instance",
        "volume-key",
        "/datasets",
    )
    assert result["root"] == {
        "display_name": "datasets",
        "path": "/datasets",
        "type": "FOLDER",
        "children": [
            {
                "display_name": "train.jsonl",
                "path": "/datasets/train.jsonl",
                "type": "FILE",
                "time_created": "created",
                "time_updated": "updated",
            }
        ],
    }


def test_list_volume_files_inspects_folders_when_recursive_listing_is_shallow(
    monkeypatch,
):
    """Direct-child AI DP responses are expanded into the promised tree."""
    workflow_service = service.AidpWorkflowService(settings=SimpleNamespace())
    catalogs, schemas, volumes = Mock(), Mock(), Mock()
    catalog = SimpleNamespace(key="catalog-key", display_name="catalog")
    schema = SimpleNamespace(key="catalog.schema", display_name="schema")
    volume = SimpleNamespace(key="volume-key", display_name="volume")

    @contextmanager
    def catalog_clients():
        yield "instance", catalogs, schemas, volumes

    monkeypatch.setattr(workflow_service, "_catalog_clients", catalog_clients)
    monkeypatch.setattr(
        service.oci.pagination,
        "list_call_get_all_results",
        Mock(
            side_effect=[
                SimpleNamespace(data=[catalog]),
                SimpleNamespace(data=[schema]),
                SimpleNamespace(data=[volume]),
            ]
        ),
    )
    volumes.list_files.side_effect = [
        SimpleNamespace(
            data=SimpleNamespace(
                items=[
                    SimpleNamespace(
                        display_name="datasets",
                        path="/datasets",
                        type="FOLDER",
                        time_created="created",
                        time_updated="updated",
                    )
                ]
            ),
            headers={},
        ),
        SimpleNamespace(
            data=SimpleNamespace(
                items=[
                    SimpleNamespace(
                        display_name="train.jsonl",
                        path="/datasets/train.jsonl",
                        type="FILE",
                        time_created="created",
                        time_updated="updated",
                    )
                ]
            ),
            headers={},
        ),
    ]

    result = workflow_service.list_volume_files("catalog", "schema", "volume")

    assert [call.args[2] for call in volumes.list_files.call_args_list] == [
        "/",
        "/datasets",
    ]
    assert result["root"]["children"][0]["children"][0]["path"] == (
        "/datasets/train.jsonl"
    )
    assert result["is_truncated"] is False


def test_list_volume_files_accepts_an_already_relative_response_path(monkeypatch):
    """AI DP responses without a mount prefix remain valid volume paths."""
    workflow_service = service.AidpWorkflowService(settings=SimpleNamespace())
    catalogs, schemas, volumes = Mock(), Mock(), Mock()
    catalog = SimpleNamespace(key="catalog-key", display_name="catalog")
    schema = SimpleNamespace(key="catalog.schema", display_name="schema")
    volume = SimpleNamespace(key="volume-key", display_name="volume")

    @contextmanager
    def catalog_clients():
        yield "instance", catalogs, schemas, volumes

    monkeypatch.setattr(workflow_service, "_catalog_clients", catalog_clients)
    monkeypatch.setattr(
        service.oci.pagination,
        "list_call_get_all_results",
        Mock(
            side_effect=[
                SimpleNamespace(data=[catalog]),
                SimpleNamespace(data=[schema]),
                SimpleNamespace(data=[volume]),
            ]
        ),
    )
    volumes.list_files.return_value = SimpleNamespace(
        data=SimpleNamespace(
            items=[
                SimpleNamespace(
                    display_name="train.jsonl",
                    path="/datasets/train.jsonl",
                    type="FILE",
                    time_created="created",
                    time_updated="updated",
                )
            ]
        ),
        headers={},
    )

    result = workflow_service.list_volume_files(
        "catalog", "schema", "volume", path="/datasets"
    )

    assert result["root"]["children"][0]["path"] == "/datasets/train.jsonl"


@pytest.mark.parametrize("value", [0, 1001, True, "100"])
def test_volume_tools_reject_unsafe_result_limits(value):
    """Volume tool bounds are enforced before remote discovery."""
    workflow_service = service.AidpWorkflowService(settings=SimpleNamespace())

    with pytest.raises(AidpError, match="max_results"):
        workflow_service.list_catalog_volumes("catalog", max_results=value)
    with pytest.raises(AidpError, match="max_results"):
        workflow_service.list_volume_files(
            "catalog", "schema", "volume", max_results=value
        )


@pytest.mark.parametrize("value", [0, 12001, True, "12"])
def test_get_job_run_output_rejects_unsafe_character_limits(value):
    """The local output character bound is validated before cloud discovery."""
    workflow_service = service.AidpWorkflowService(settings=SimpleNamespace())

    with pytest.raises(AidpError, match="max_characters"):
        workflow_service.get_job_run_output("job-run-key", value)


def test_server_registers_the_twelve_scoped_tools():
    """The MCP schema exposes the specified tools without cloud access."""
    names = {tool.name for tool in asyncio.run(MCP.list_tools())}
    assert names == {
        "upload_notebook",
        "list_notebooks",
        "find_notebook_jobs",
        "list_catalog_volumes",
        "list_volume_files",
        "list_job_runs",
        "ensure_notebook_job",
        "start_notebook_job",
        "get_job_run",
        "get_cluster_status",
        "set_cluster_state",
        "get_job_run_output",
    }


def test_mcp_tool_contract_matches_snapshot():
    """The public tool names, descriptions, and schemas remain deliberate."""
    expected = json.loads(
        (Path(__file__).parent / "fixtures" / "mcp_tools.json").read_text(
            encoding="utf-8"
        )
    )
    tools = asyncio.run(MCP.list_tools())
    actual = [
        {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.parameters,
        }
        for tool in tools
    ]

    assert actual == expected
