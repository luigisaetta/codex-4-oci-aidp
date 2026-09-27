"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: AI DP notebook upload planning, content access, and notebook listing.
"""

from pathlib import PurePosixPath

import oci
from aidp_python_client.aidataplatform_dp import models

from aidp_common.connection import AidpError
from aidp_mcp.local_files import PROJECT_ROOT, validate_local_notebook
from aidp_mcp.lookups import next_page
from aidp_mcp.safety import should_apply
from aidp_mcp.targets import workspace_clients
from aidp_mcp.validation import (
    encoded_content_path,
    validate_workspace_directory,
    validate_workspace_path,
    workspace_content_path,
)

MAX_NOTEBOOK_LIST_RESULTS = 1000


def notebook_content_request(
    notebooks, *, instance_id, workspace_key, method, content_path, body=None
):
    """Call the documented notebook-content endpoint without double encoding.

    The generated Python client turns `%2F` into `%252F` when an encoded path
    is supplied as a path parameter. The notebook service requires encoded
    slashes inside its final URL segment.

    Args:
        notebooks: Generated notebook client with the configured signer.
        instance_id: Selected AI DP instance OCID.
        workspace_key: Selected workspace key.
        method: HTTP method accepted by the notebook contents endpoint.
        content_path: Absolute notebook service path below `/Workspace`.
        body: Optional generated SDK request model.

    Returns:
        oci.response.Response: Notebook content service response.
    """
    return notebooks.base_client.call_api(
        resource_path=(
            "/aiDataPlatforms/{aiDataPlatformId}/workspaces/{workspaceKey}"
            f"/notebook/api/contents/{encoded_content_path(content_path)}"
        ),
        method=method,
        path_params={
            "aiDataPlatformId": instance_id,
            "workspaceKey": workspace_key,
        },
        header_params={
            "accept": "application/json",
            "content-type": "application/json",
        },
        body=body,
        response_type="Content",
    )


def workspace_objects_request(
    notebooks, *, instance_id, workspace_key, path, limit, page=None
):
    """List notebook workspace-object summaries through the documented API.

    Args:
        notebooks: Generated notebook client with configured signer and endpoint.
        instance_id: Selected AI DP instance OCID.
        workspace_key: Selected workspace key.
        path: Absolute workspace directory path.
        limit: Maximum summaries to return in this page.
        page: Optional OCI page token from the preceding response.

    Returns:
        oci.response.Response: A page of ``WorkspaceObjectCollection`` data.
    """
    query_params = {"path": path, "type": "NOTEBOOK", "limit": limit}
    if page:
        query_params["page"] = page
    return notebooks.base_client.call_api(
        resource_path=(
            "/aiDataPlatforms/{aiDataPlatformId}/workspaces/{workspaceKey}/objects"
        ),
        method="GET",
        path_params={
            "aiDataPlatformId": instance_id,
            "workspaceKey": workspace_key,
        },
        query_params=query_params,
        header_params={"accept": "application/json"},
        response_type="WorkspaceObjectCollection",
    )


def is_missing_content_error(error):
    """Identify an absent-notebook response from the AI DP content service.

    Args:
        error: OCI service error from a notebook-content GET request.

    Returns:
        bool: Whether the response is the observed absent-content form.
    """
    return error.status == 404 or (
        error.status == 500
        and getattr(error, "code", None) == "InternalError"
        and "getting notebook content" in str(getattr(error, "message", "")).lower()
    )


def is_existing_folder_error(error):
    """Identify AI DP's conflict response for a pre-existing workspace folder.

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


def create_workspace_folder(notebooks, instance_id, workspace_key, folder_path):
    """Create or retain one workspace folder through the documented objects API.

    Args:
        notebooks: Generated notebook client with the configured endpoint and
            signer.
        instance_id: Selected AI DP instance OCID.
        workspace_key: Selected workspace key.
        folder_path: Absolute folder path below `/Workspace`.

    Raises:
        AidpError: The service does not accept the folder creation request.
    """
    try:
        response = notebooks.base_client.call_api(
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


def _notebook_summary(item):
    """Return selected notebook metadata without creator, tags, or content."""
    return {
        "display_name": getattr(item, "display_name", None),
        "path": getattr(item, "path", None),
        "type": getattr(item, "type", None),
        "time_created": getattr(item, "time_created", None),
        "time_updated": getattr(item, "time_updated", None),
    }


def upload_notebook(settings, local_path, workspace_path, overwrite=False, apply=False):
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
    roots = getattr(settings, "allowed_roots", (PROJECT_ROOT,))
    local_file, local_root, content, digest = validate_local_notebook(local_path, roots)
    destination = validate_workspace_path(workspace_path)
    service_path = workspace_content_path(destination)
    with workspace_clients(settings) as clients:
        instance_id, workspace_key, _, notebooks, _ = clients
        try:
            remote = notebook_content_request(
                notebooks,
                instance_id=instance_id,
                workspace_key=workspace_key,
                method="GET",
                content_path=service_path,
            ).data
            remote_digest = getattr(remote, "hash", None)
            same = (
                getattr(remote, "hash_algorithm", "").lower().replace("-", "")
                == "sha256"
                and remote_digest == digest
            )
            action = "unchanged" if same else "update"
        except oci.exceptions.ServiceError as exc:
            if not is_missing_content_error(exc):
                raise
            action = "create"
        result = {
            "action": action,
            "apply": apply,
            "local_path": str(local_file.relative_to(local_root)),
            "local_root": local_root.name,
            "workspace_path": destination,
            "sha256": digest,
        }
        if not should_apply(apply, action):
            return result
        if action == "update" and not overwrite:
            raise AidpError("Remote notebook exists; set overwrite=true to replace it.")
        if action == "create":
            create_workspace_folder(
                notebooks,
                instance_id,
                workspace_key,
                str(PurePosixPath(service_path).parent),
            )
            created = notebook_content_request(
                notebooks,
                instance_id=instance_id,
                workspace_key=workspace_key,
                method="POST",
                content_path=str(PurePosixPath(service_path).parent),
                body=models.CreateContentDetails(ext=".ipynb", type="notebook"),
            ).data
            created_path = getattr(created, "path", None)
            if not isinstance(created_path, str) or not created_path:
                raise AidpError("Notebook creation response is missing its path.")
            notebook_content_request(
                notebooks,
                instance_id=instance_id,
                workspace_key=workspace_key,
                method="PATCH",
                content_path=created_path,
                body=models.ModifyContentDetails(path=service_path),
            )
        notebook_content_request(
            notebooks,
            instance_id=instance_id,
            workspace_key=workspace_key,
            method="PUT",
            content_path=service_path,
            body=models.UpdateContentDetails(
                name=PurePosixPath(destination).name,
                path=service_path,
                type=models.UpdateContentDetails.TYPE_NOTEBOOK,
                content=content,
                format=models.UpdateContentDetails.FORMAT_JSON,
            ),
        )
        return result


def list_notebooks(settings, path="/Workspace", name_contains=None, max_results=100):
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
    directory = validate_workspace_directory(path)
    if name_contains is not None and not isinstance(name_contains, str):
        raise AidpError("name_contains must be a string or null.")
    if not isinstance(max_results, int) or isinstance(max_results, bool):
        raise AidpError("max_results must be an integer from 1 through 1000.")
    if not 1 <= max_results <= MAX_NOTEBOOK_LIST_RESULTS:
        raise AidpError("max_results must be an integer from 1 through 1000.")

    match = name_contains.casefold() if name_contains else None
    summaries = []
    page = None
    with workspace_clients(settings) as clients:
        instance_id, workspace_key, _, notebooks, _ = clients
        while len(summaries) < max_results:
            response = workspace_objects_request(
                notebooks,
                instance_id=instance_id,
                workspace_key=workspace_key,
                path=directory,
                limit=max_results - len(summaries),
                page=page,
            )
            for item in getattr(response.data, "items", None) or []:
                display_name = getattr(item, "display_name", None)
                if match and (
                    not isinstance(display_name, str)
                    or match not in display_name.casefold()
                ):
                    continue
                summaries.append(_notebook_summary(item))
                if len(summaries) == max_results:
                    break
            page = next_page(response)
            if page:
                continue
            break
    return {
        "path": directory,
        "name_contains": name_contains,
        "notebooks": summaries,
        "is_truncated": bool(page),
    }
