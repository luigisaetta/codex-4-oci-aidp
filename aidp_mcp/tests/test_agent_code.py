"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Offline tests for AI DP agent code upload and definition planning.
"""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aidp_common.connection import AidpError
from aidp_mcp import agent_code
from aidp_mcp.tests.agent_fixtures import (
    collection_response as _response,
    make_agent as _agent,
)


@contextmanager
def _clients(client):
    """Provide a minimal agent-client context for one offline test."""
    yield "instance", "workspace", client


@contextmanager
def _workspace_clients(objects):
    """Provide the workspace-object client contract for agent code tests."""
    yield "instance", "workspace", Mock(), Mock(), Mock(), objects


def _agent_source(tmp_path, files):
    """Create a safe local agent source tree from relative byte payloads."""
    directory = tmp_path / "hello_agent"
    for relative_path, data in files.items():
        file_path = directory / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(data)
    return directory


def _upload_settings(root):
    """Return the minimum allowed-root settings used by upload tests."""
    return SimpleNamespace(allowed_roots=(root,))


def test_upload_agent_code_plans_create_update_unchanged_and_remote_only(
    tmp_path, monkeypatch
):
    """The default plan compares raw bytes and performs no workspace writes."""
    directory = _agent_source(
        tmp_path, {"hello.py": b"same", "nested/new.py": b"new", "changed.py": b"local"}
    )
    objects = Mock()
    remote = {
        "/Workspace/hello/hello.py": b"same",
        "/Workspace/hello/changed.py": b"remote",
        "/Workspace/hello/nested/new.py": None,
    }
    monkeypatch.setattr(
        agent_code, "workspace_clients", lambda _settings: _workspace_clients(objects)
    )
    monkeypatch.setattr(
        agent_code,
        "read_workspace_file",
        lambda _client, _instance, _workspace, path: remote[path],
    )
    remote_item = SimpleNamespace(path="/Workspace/hello/remote.py", type="FILE")
    monkeypatch.setattr(
        agent_code, "list_workspace_objects", lambda *_args: ([remote_item], False)
    )
    create_folder = Mock()
    upload = Mock()
    monkeypatch.setattr(agent_code, "create_workspace_folder", create_folder)
    monkeypatch.setattr(agent_code, "upload_workspace_file", upload)

    result = agent_code.upload_agent_code(
        _upload_settings(tmp_path), directory, "/Workspace/hello"
    )

    assert result["counts"] == {"create": 1, "update": 1, "unchanged": 1}
    assert [entry["relative_path"] for entry in result["files"]] == [
        "changed.py",
        "hello.py",
        "nested/new.py",
    ]
    assert result["remote_only"] == ["remote.py"]
    assert result["apply"] is False
    assert all(len(entry["sha256"]) == 12 for entry in result["files"])
    create_folder.assert_not_called()
    upload.assert_not_called()


def test_upload_agent_code_rejects_updates_before_any_write(tmp_path, monkeypatch):
    """An update without explicit overwrite cannot create folders or files."""
    directory = _agent_source(tmp_path, {"hello.py": b"local"})
    objects = Mock()
    monkeypatch.setattr(
        agent_code, "workspace_clients", lambda _settings: _workspace_clients(objects)
    )
    monkeypatch.setattr(agent_code, "read_workspace_file", Mock(return_value=b"remote"))
    monkeypatch.setattr(
        agent_code, "list_workspace_objects", lambda *_args: ([], False)
    )
    create_folder = Mock()
    upload = Mock()
    monkeypatch.setattr(agent_code, "create_workspace_folder", create_folder)
    monkeypatch.setattr(agent_code, "upload_workspace_file", upload)

    with pytest.raises(AidpError, match="overwrite=true"):
        agent_code.upload_agent_code(
            _upload_settings(tmp_path), directory, "/Workspace/hello", apply=True
        )

    create_folder.assert_not_called()
    upload.assert_not_called()


def test_upload_agent_code_creates_parent_folders_and_verifies_files(
    tmp_path, monkeypatch
):
    """Apply creates folders in order, skips unchanged files, and reads uploads back."""
    directory = _agent_source(
        tmp_path, {"unchanged.py": b"same", "nested/new.py": b"new"}
    )
    objects = Mock()
    read_counts = {"/Workspace/hello/nested/new.py": 0}

    def read(_client, _instance, _workspace, path):
        if path.endswith("unchanged.py"):
            return b"same"
        read_counts[path] += 1
        return None if read_counts[path] == 1 else b"new"

    create_folder = Mock()
    upload = Mock()
    monkeypatch.setattr(
        agent_code, "workspace_clients", lambda _settings: _workspace_clients(objects)
    )
    monkeypatch.setattr(agent_code, "read_workspace_file", read)
    monkeypatch.setattr(
        agent_code, "list_workspace_objects", lambda *_args: ([], False)
    )
    monkeypatch.setattr(agent_code, "create_workspace_folder", create_folder)
    monkeypatch.setattr(agent_code, "upload_workspace_file", upload)

    result = agent_code.upload_agent_code(
        _upload_settings(tmp_path), directory, "/Workspace/hello", apply=True
    )

    assert [call.args[3] for call in create_folder.call_args_list] == [
        "/Workspace/hello",
        "/Workspace/hello/nested",
    ]
    upload.assert_called_once_with(
        objects,
        "instance",
        "workspace",
        "/Workspace/hello/nested/new.py",
        b"new",
        overwrite=False,
    )
    assert result["uploaded"] == result["verified"] == 1


def test_upload_agent_code_stops_on_readback_mismatch_and_lists_uploads(
    tmp_path, monkeypatch
):
    """A mismatched read-back fails safely after reporting the uploaded file."""
    directory = _agent_source(tmp_path, {"hello.py": b"local"})
    objects = Mock()
    monkeypatch.setattr(
        agent_code, "workspace_clients", lambda _settings: _workspace_clients(objects)
    )
    monkeypatch.setattr(
        agent_code, "read_workspace_file", Mock(side_effect=[None, b"wrong"])
    )
    monkeypatch.setattr(
        agent_code, "list_workspace_objects", lambda *_args: ([], False)
    )
    monkeypatch.setattr(agent_code, "create_workspace_folder", Mock())
    monkeypatch.setattr(agent_code, "upload_workspace_file", Mock())

    with pytest.raises(AidpError, match="hello.py; uploaded files: hello.py"):
        agent_code.upload_agent_code(
            _upload_settings(tmp_path), directory, "/Workspace/hello", apply=True
        )


def test_upload_agent_code_reports_completed_files_after_mid_upload_failure(
    tmp_path, monkeypatch
):
    """A later upload failure leaves a rerunnable report of earlier uploads."""
    directory = _agent_source(tmp_path, {"first.py": b"first", "second.py": b"second"})
    objects = Mock()
    read = Mock(side_effect=[None, None, b"first"])
    upload = Mock(side_effect=[None, AidpError("upload rejected")])
    monkeypatch.setattr(
        agent_code, "workspace_clients", lambda _settings: _workspace_clients(objects)
    )
    monkeypatch.setattr(agent_code, "read_workspace_file", read)
    monkeypatch.setattr(
        agent_code, "list_workspace_objects", lambda *_args: ([], False)
    )
    monkeypatch.setattr(agent_code, "create_workspace_folder", Mock())
    monkeypatch.setattr(agent_code, "upload_workspace_file", upload)

    with pytest.raises(AidpError, match="second.py; uploaded files: first.py"):
        agent_code.upload_agent_code(
            _upload_settings(tmp_path), directory, "/Workspace/hello", apply=True
        )


def test_upload_agent_code_reports_only_unchanged_after_a_successful_rerun(
    tmp_path, monkeypatch
):
    """A fully matching apply is a no-op with explicit zero upload counters."""
    directory = _agent_source(tmp_path, {"hello.py": b"same"})
    objects = Mock()
    monkeypatch.setattr(
        agent_code, "workspace_clients", lambda _settings: _workspace_clients(objects)
    )
    monkeypatch.setattr(agent_code, "read_workspace_file", Mock(return_value=b"same"))
    monkeypatch.setattr(
        agent_code, "list_workspace_objects", lambda *_args: ([], False)
    )
    upload = Mock()
    monkeypatch.setattr(agent_code, "upload_workspace_file", upload)

    result = agent_code.upload_agent_code(
        _upload_settings(tmp_path), directory, "/Workspace/hello", apply=True
    )

    assert result["counts"] == {"create": 0, "update": 0, "unchanged": 1}
    assert result["uploaded"] == result["verified"] == 0
    upload.assert_not_called()


def _definition_contexts(monkeypatch, objects, client):
    """Install independent workspace-file and agent-definition client contexts."""
    monkeypatch.setattr(
        agent_code, "workspace_clients", lambda _settings: _workspace_clients(objects)
    )
    monkeypatch.setattr(agent_code, "agent_clients", lambda _settings: _clients(client))


def test_ensure_agent_plans_and_creates_exact_code_definition(monkeypatch):
    """Creation uses only documented CODE fields and never sets compute metadata."""
    objects = Mock()
    client = Mock()
    client.list_agents.return_value = _response([])
    _definition_contexts(monkeypatch, objects, client)
    monkeypatch.setattr(agent_code, "read_workspace_file", Mock(return_value=b"source"))
    refreshed = {"name": "hello", "type": "CODE"}
    monkeypatch.setattr(agent_code, "get_agent_response", Mock(return_value=refreshed))

    result = agent_code.ensure_agent(
        SimpleNamespace(),
        "hello",
        "/Workspace/hello",
        "hello.py",
        dependencies_file="requirements.txt",
        description="A hello agent",
        apply=True,
    )

    assert result == refreshed
    created = client.create_agent.call_args.args[2]
    assert created.display_name == "hello"
    assert created.type == "CODE"
    assert created.path_info == "/Workspace"
    assert created.entry_file_path == "/Workspace/hello/hello.py"
    assert created.dependencies_file_path == "/Workspace/hello/requirements.txt"
    assert created.description == "A hello agent"
    assert created.compute_key is None


def test_ensure_agent_plan_is_unchanged_without_description_override(monkeypatch):
    """An omitted description preserves the existing CODE agent description."""
    objects = Mock()
    detail = _agent(
        entry_file_path="/Workspace/hello/hello.py",
        dependencies_file_path="/Workspace/hello/requirements.txt",
        description="Existing description",
        deployment_mode="NOT_DEPLOYED",
    )
    client = Mock()
    client.list_agents.return_value = _response([detail])
    client.get_agent.return_value = SimpleNamespace(data=detail, headers={})
    _definition_contexts(monkeypatch, objects, client)
    monkeypatch.setattr(agent_code, "read_workspace_file", Mock(return_value=b"source"))

    result = agent_code.ensure_agent(
        SimpleNamespace(),
        "hello",
        "/Workspace/hello",
        "hello.py",
        dependencies_file="requirements.txt",
    )

    assert result["action"] == "unchanged"
    assert result["desired"]["description"] == "Existing description"
    client.create_agent.assert_not_called()
    client.update_agent.assert_not_called()


def test_ensure_agent_updates_only_changed_fields_with_etag(monkeypatch):
    """A CODE update sends only intended changed fields and optimistic ETag."""
    objects = Mock()
    detail = _agent(
        entry_file_path="/Workspace/hello/old.py",
        dependencies_file_path="/Workspace/hello/requirements.txt",
        description="Old description",
    )
    client = Mock()
    client.list_agents.return_value = _response([detail])
    client.get_agent.return_value = SimpleNamespace(
        data=detail, headers={"etag": "etag"}
    )
    _definition_contexts(monkeypatch, objects, client)
    monkeypatch.setattr(agent_code, "read_workspace_file", Mock(return_value=b"source"))
    refreshed = {"name": "hello", "entry_file_path": "/Workspace/hello/new.py"}
    monkeypatch.setattr(agent_code, "get_agent_response", Mock(return_value=refreshed))

    result = agent_code.ensure_agent(
        SimpleNamespace(),
        "hello",
        "/Workspace/hello",
        "new.py",
        dependencies_file="requirements.txt",
        description="New description",
        apply=True,
    )

    assert result == refreshed
    updated = client.update_agent.call_args.args[3]
    assert updated.entry_file_path == "/Workspace/hello/new.py"
    assert updated.description == "New description"
    assert updated.dependencies_file_path is None
    assert client.update_agent.call_args.kwargs == {"if_match": "etag"}


def test_ensure_agent_refuses_canvas_agents_and_missing_workspace_files(monkeypatch):
    """Canvas agents are preserved and missing source files stop before agent reads."""
    objects = Mock()
    canvas = _agent(type="CANVAS")
    client = Mock()
    client.list_agents.return_value = _response([canvas])
    client.get_agent.return_value = SimpleNamespace(data=canvas, headers={})
    _definition_contexts(monkeypatch, objects, client)
    monkeypatch.setattr(agent_code, "read_workspace_file", Mock(return_value=b"source"))

    with pytest.raises(AidpError, match="not CODE"):
        agent_code.ensure_agent(
            SimpleNamespace(), "hello", "/Workspace/hello", "hello.py"
        )

    client.create_agent.assert_not_called()
    client.update_agent.assert_not_called()
    missing_client = Mock()
    _definition_contexts(monkeypatch, objects, missing_client)
    monkeypatch.setattr(agent_code, "read_workspace_file", Mock(return_value=None))
    with pytest.raises(AidpError, match="entry_file does not exist"):
        agent_code.ensure_agent(
            SimpleNamespace(), "hello", "/Workspace/hello", "hello.py"
        )
    missing_client.list_agents.assert_not_called()


def test_ensure_agent_plan_notes_redeploy_for_a_deployed_code_agent(monkeypatch):
    """A deployed CODE agent plan warns that this tool never redeploys it."""
    objects = Mock()
    detail = _agent(
        entry_file_path="/Workspace/hello/old.py",
        dependencies_file_path=None,
        description=None,
        deployment_mode="MANUAL",
    )
    client = Mock()
    client.list_agents.return_value = _response([detail])
    client.get_agent.return_value = SimpleNamespace(data=detail, headers={})
    _definition_contexts(monkeypatch, objects, client)
    monkeypatch.setattr(agent_code, "read_workspace_file", Mock(return_value=b"source"))

    result = agent_code.ensure_agent(
        SimpleNamespace(), "hello", "/Workspace/hello", "new.py"
    )

    assert result["action"] == "update"
    assert "redeploy" in result["redeploy_note"]
