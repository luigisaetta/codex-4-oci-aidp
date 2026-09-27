"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Offline tests for shared AI DP workspace file operations.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import oci
import pytest

from aidp_common.connection import AidpError
from aidp_mcp import workspace_files


def test_read_workspace_file_uses_encoded_final_path_and_raw_bytes():
    """Workspace reads encode the full path only in the final API segment."""
    client = SimpleNamespace(base_client=Mock())
    client.base_client.call_api.return_value = SimpleNamespace(data=b"contents")

    result = workspace_files.read_workspace_file(
        client, "instance", "workspace", "/Workspace/agent/hello.py"
    )

    assert result == b"contents"
    arguments = client.base_client.call_api.call_args.kwargs
    assert arguments["method"] == "GET"
    assert arguments["resource_path"].endswith(
        "/objects/%2FWorkspace%2Fagent%2Fhello.py"
    )
    assert arguments["header_params"] == {"accept": "*/*"}
    assert arguments["response_type"] == "stream"


def test_read_workspace_file_accepts_stream_response_content_bytes():
    """Stream responses expose bytes through content rather than read()."""
    client = SimpleNamespace(base_client=Mock())
    client.base_client.call_api.return_value = SimpleNamespace(
        data=SimpleNamespace(content=b"contents")
    )

    result = workspace_files.read_workspace_file(
        client, "instance", "workspace", "/Workspace/agent/hello.py"
    )

    assert result == b"contents"


def test_read_workspace_file_returns_none_only_for_a_missing_file():
    """A 404 is a missing file while other workspace service errors propagate."""
    client = SimpleNamespace(base_client=Mock())
    client.base_client.call_api.side_effect = oci.exceptions.ServiceError(
        404, "NotFound", {}, "missing"
    )

    assert (
        workspace_files.read_workspace_file(
            client, "instance", "workspace", "/Workspace/agent/hello.py"
        )
        is None
    )


def test_upload_workspace_file_sends_binary_file_request_without_retry():
    """File uploads use the documented object headers and raw binary body."""
    client = SimpleNamespace(base_client=Mock())
    client.base_client.call_api.return_value = SimpleNamespace(status=201)

    workspace_files.upload_workspace_file(
        client,
        "instance",
        "workspace",
        "/Workspace/agent/hello.py",
        b"data",
        overwrite=True,
    )

    arguments = client.base_client.call_api.call_args.kwargs
    assert arguments["method"] == "POST"
    assert arguments["header_params"] == {
        "accept": "*/*",
        "content-type": "application/octet-stream",
        "path": "/Workspace/agent/hello.py",
        "type": "FILE",
        "is-overwrite": "true",
    }
    assert arguments["body"] == b"data"


def test_create_workspace_folder_keeps_existing_folder_idempotently():
    """The moved folder helper preserves notebook upload conflict behavior."""
    client = SimpleNamespace(base_client=Mock())
    client.base_client.call_api.side_effect = oci.exceptions.ServiceError(
        409, "Conflict", {}, "Directory already exists"
    )

    workspace_files.create_workspace_folder(
        client, "instance", "workspace", "/Workspace/agent"
    )


@pytest.mark.parametrize("path", ["/Workspace/agent/../hello.py", "/Workspace/a\n.py"])
def test_workspace_file_operations_reject_unsafe_workspace_paths(path):
    """Traversal and control characters never reach the workspace API."""
    client = SimpleNamespace(base_client=Mock())

    with pytest.raises(AidpError):
        workspace_files.read_workspace_file(client, "instance", "workspace", path)

    client.base_client.call_api.assert_not_called()


def test_list_workspace_objects_paginates_with_the_shared_sdk_page_limit():
    """Remote-only discovery never sends a workspace-object list above 100."""
    client = Mock()
    first = SimpleNamespace(
        data=SimpleNamespace(items=[SimpleNamespace(path="/Workspace/a.py")]),
        headers={"opc-next-page": "next"},
    )
    second = SimpleNamespace(
        data=SimpleNamespace(items=[SimpleNamespace(path="/Workspace/b.py")]),
        headers={},
    )
    client.list_workspace_objects.side_effect = [first, second]

    items, truncated = workspace_files.list_workspace_objects(
        client, "instance", "workspace", "/Workspace", 101
    )

    assert [item.path for item in items] == ["/Workspace/a.py", "/Workspace/b.py"]
    assert truncated is True
    assert all(
        call.kwargs["limit"] <= 100
        for call in client.list_workspace_objects.call_args_list
    )
