"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Shared safe workspace folder and binary file operations.
"""

import oci

from aidp_common.connection import AidpError
from aidp_mcp.lookups import SDK_PAGE_SIZE, next_page
from aidp_mcp.validation import encoded_content_path, validate_workspace_directory


def is_existing_folder_error(error):
    """Identify AI DP's conflict response for an existing workspace folder.

    Args:
        error: OCI service error from the workspace objects API.

    Returns:
        bool: Whether the service reports that the requested directory exists.
    """
    return (
        error.status == 409
        and getattr(error, "code", None) == "Conflict"
        and "directory already exists" in str(getattr(error, "message", "")).lower()
    )


def create_workspace_folder(client, instance_id, workspace_key, folder_path):
    """Create or retain one validated workspace folder without retries.

    Args:
        client: Generated workspace-object client with configured signing.
        instance_id: Selected AI DP instance OCID.
        workspace_key: Selected workspace key.
        folder_path: Absolute folder path below `/Workspace`.

    Raises:
        AidpError: The service does not accept the folder creation request.
    """
    folder_path = _validate_workspace_object_path(folder_path)
    try:
        response = client.base_client.call_api(
            resource_path=(
                "/aiDataPlatforms/{aiDataPlatformId}/workspaces/{workspaceKey}/objects"
            ),
            method="POST",
            path_params={
                "aiDataPlatformId": instance_id,
                "workspaceKey": workspace_key,
            },
            header_params={
                "accept": "*/*",
                "content-type": "application/octet-stream",
                "path": folder_path,
                "type": "FOLDER",
                "is-overwrite": "true",
            },
            body=b"",
            response_type=None,
        )
    except oci.exceptions.ServiceError as exc:
        if is_existing_folder_error(exc):
            return
        raise
    if response.status not in (200, 201):
        raise AidpError(f"Workspace folder creation returned HTTP {response.status}.")


def read_workspace_file(client, instance_id, workspace_key, path):
    """Return raw workspace-file bytes, or ``None`` when the file is absent.

    Args:
        client: Generated workspace-object client with configured signing.
        instance_id: Selected AI DP instance OCID.
        workspace_key: Selected workspace key.
        path: Absolute file path below `/Workspace`.

    Returns:
        bytes | None: Raw file bytes, or ``None`` for HTTP 404.

    Raises:
        AidpError: The path or raw service response is invalid.
        oci.exceptions.ServiceError: The service returns an error other than 404.
    """
    path = _validate_workspace_object_path(path)
    try:
        response = client.base_client.call_api(
            resource_path=(
                "/aiDataPlatforms/{aiDataPlatformId}/workspaces/{workspaceKey}/objects/"
                f"{encoded_content_path(path)}"
            ),
            method="GET",
            path_params={
                "aiDataPlatformId": instance_id,
                "workspaceKey": workspace_key,
            },
            header_params={"accept": "*/*"},
            response_type="stream",
        )
    except oci.exceptions.ServiceError as exc:
        if exc.status == 404:
            return None
        raise
    return _raw_response_bytes(response.data)


def upload_workspace_file(client, instance_id, workspace_key, path, data, *, overwrite):
    """Upload one binary workspace file without retrying the request.

    Args:
        client: Generated workspace-object client with configured signing.
        instance_id: Selected AI DP instance OCID.
        workspace_key: Selected workspace key.
        path: Absolute file path below `/Workspace`.
        data: Raw file bytes.
        overwrite: Whether an existing file may be replaced.

    Raises:
        AidpError: Inputs are invalid or the service rejects the upload.
    """
    path = _validate_workspace_object_path(path)
    if not isinstance(data, bytes):
        raise AidpError("Workspace file content must be raw bytes.")
    if overwrite is not True and overwrite is not False:
        raise AidpError("overwrite must be a boolean.")
    response = client.base_client.call_api(
        resource_path=(
            "/aiDataPlatforms/{aiDataPlatformId}/workspaces/{workspaceKey}/objects"
        ),
        method="POST",
        path_params={
            "aiDataPlatformId": instance_id,
            "workspaceKey": workspace_key,
        },
        header_params={
            "accept": "*/*",
            "content-type": "application/octet-stream",
            "path": path,
            "type": "FILE",
            "is-overwrite": str(overwrite).lower(),
        },
        body=data,
        response_type=None,
    )
    if response.status not in (200, 201):
        raise AidpError(f"Workspace file upload returned HTTP {response.status}.")


def list_workspace_objects(client, instance_id, workspace_key, path, max_results):
    """List bounded object summaries in one validated workspace directory.

    Args:
        client: Generated workspace-object client with configured signing.
        instance_id: Selected AI DP instance OCID.
        workspace_key: Selected workspace key.
        path: Absolute directory path below `/Workspace`.
        max_results: Maximum summaries returned, from 1 through 1,000.

    Returns:
        tuple[list, bool]: Object summaries and whether more results exist.

    Raises:
        AidpError: The directory or result limit is invalid.
    """
    path = _validate_workspace_object_path(path)
    if (
        isinstance(max_results, bool)
        or not isinstance(max_results, int)
        or not 1 <= max_results <= 1000
    ):
        raise AidpError("max_results must be an integer from 1 through 1000.")
    items = []
    page = None
    truncated = False
    while len(items) < max_results:
        try:
            response = client.list_workspace_objects(
                instance_id,
                workspace_key,
                path,
                limit=min(SDK_PAGE_SIZE, max_results - len(items)),
                page=page,
            )
        except oci.exceptions.ServiceError as exc:
            if exc.status == 404:
                return [], False
            raise
        page_items = getattr(getattr(response, "data", None), "items", None) or []
        for index, item in enumerate(page_items):
            items.append(item)
            if len(items) == max_results:
                truncated = index < len(page_items) - 1
                break
        next_token = next_page(response)
        if next_token is None:
            break
        page = next_token
        truncated = True
    return items, truncated


def _validate_workspace_object_path(path):
    """Validate a workspace object path and reject control characters."""
    path = validate_workspace_directory(path)
    if any(character.isspace() and character not in (" ", "\t") for character in path):
        raise AidpError("Workspace paths must not contain control characters.")
    if any(ord(character) < 32 or ord(character) == 127 for character in path):
        raise AidpError("Workspace paths must not contain control characters.")
    return path


def _raw_response_bytes(data):
    """Normalize an OCI raw response payload to bytes without decoding it."""
    if isinstance(data, bytes):
        return data
    content = getattr(data, "content", None)
    if isinstance(content, bytes):
        return content
    if hasattr(data, "read"):
        content = data.read()
        if isinstance(content, bytes):
            return content
    raise AidpError("Workspace file read returned a non-binary response.")
