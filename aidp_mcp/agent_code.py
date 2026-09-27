"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Guarded AI DP agent code upload and CODE-definition reconciliation.
"""

import hashlib
from pathlib import PurePosixPath

from aidp_python_client.aidataplatform_dp import models

from aidp_common.connection import AidpError
from aidp_mcp.agent_lookup import find_agent_matches, get_agent_response
from aidp_mcp.local_files import (
    PROJECT_ROOT,
    collect_agent_files,
    validate_local_directory,
)
from aidp_mcp.lookups import resource_key
from aidp_mcp.safety import should_apply
from aidp_mcp.targets import agent_clients, workspace_clients
from aidp_mcp.validation import validate_resource_name, validate_workspace_directory
from aidp_mcp.workspace_files import (
    create_workspace_folder,
    list_workspace_objects,
    read_workspace_file,
    upload_workspace_file,
)

MAX_REMOTE_ONLY_FILES = 100


def upload_agent_code(
    settings, local_dir, workspace_dir, *, overwrite=False, apply=False
):
    """Plan or upload a safe local agent folder to an AI DP workspace.

    This AI DP agent code upload is plan-only by default. Updates require
    ``overwrite=true``; local secrets and symbolic links are refused before
    any remote request. Applying uploads files but never deletes remote files.

    Args:
        settings: Validated MCP connection settings.
        local_dir: Local agent directory within an allowed upload root.
        workspace_dir: Absolute destination directory below `/Workspace`.
        overwrite: Permit remote files with different content to be replaced.
        apply: Submit the planned create and update operations.

    Returns:
        dict: Bounded, content-free upload plan or verified apply result.

    Raises:
        AidpError: Local policy, planning, overwrite, upload, or verification
            fails.
    """
    if overwrite is not True and overwrite is not False:
        raise AidpError("overwrite must be a boolean.")
    if apply is not True and apply is not False:
        raise AidpError("apply must be a boolean.")
    roots = getattr(settings, "allowed_roots", (PROJECT_ROOT,))
    directory, _ = validate_local_directory(local_dir, roots)
    workspace_dir = validate_workspace_directory(workspace_dir)
    local_files = collect_agent_files(directory)
    entries = _local_agent_upload_entries(directory, workspace_dir, local_files)
    with workspace_clients(settings) as clients:
        instance_id, workspace_key, _, _, _, objects = clients
        _classify_agent_upload_entries(objects, instance_id, workspace_key, entries)
        remote_only = _remote_only_agent_files(
            objects, instance_id, workspace_key, workspace_dir, entries
        )
        result = _agent_upload_result(directory, workspace_dir, entries, remote_only)
        if not should_apply(apply, _agent_upload_action(entries)):
            return _with_upload_counts(result, apply, 0, 0)
        if any(entry["action"] == "update" for entry in entries) and not overwrite:
            raise AidpError(
                "Remote agent code differs; set overwrite=true to replace it."
            )
        _create_agent_upload_folders(
            objects, instance_id, workspace_key, workspace_dir, entries
        )
        uploaded, verified = _apply_agent_upload(
            objects, instance_id, workspace_key, entries
        )
    return _with_upload_counts(result, apply, uploaded, verified)


def ensure_agent(
    settings,
    agent_name,
    workspace_dir,
    entry_file,
    *,
    dependencies_file=None,
    description=None,
    apply=False,
):
    """Plan or reconcile one CODE agent definition without deployment.

    This AI DP agent definition operation is plan-only by default. It only
    creates or updates CODE agent file paths and an optional description; it
    never attaches compute or deploys the agent.

    Args:
        settings: Validated MCP connection settings.
        agent_name: Exact, case-sensitive agent display name.
        workspace_dir: Absolute workspace directory containing agent files.
        entry_file: Entry file path relative to ``workspace_dir``.
        dependencies_file: Optional dependency-file path relative to the folder.
        description: Optional replacement description.
        apply: Submit a planned create or minimal update.

    Returns:
        dict: A plan with current and desired fields, or refreshed agent data.

    Raises:
        AidpError: Validation, remote-file, agent selection, or update fails.
    """
    if apply is not True and apply is not False:
        raise AidpError("apply must be a boolean.")
    agent_name = validate_resource_name(agent_name, "Agent")
    workspace_dir = validate_workspace_directory(workspace_dir)
    entry_path = _agent_workspace_file_path(workspace_dir, entry_file, "entry_file")
    dependencies_path = (
        _agent_workspace_file_path(
            workspace_dir, dependencies_file, "dependencies_file"
        )
        if dependencies_file is not None
        else None
    )
    if description is not None and not isinstance(description, str):
        raise AidpError("description must be a string or null.")
    _require_agent_source_files(settings, entry_path, dependencies_path)
    with agent_clients(settings) as clients:
        instance_id, workspace_key, client = clients
        matches = find_agent_matches(client, instance_id, workspace_key, agent_name)
        plan, detail, etag = _agent_definition_plan(
            client,
            instance_id,
            workspace_key,
            matches,
            agent_name=agent_name,
            entry_path=entry_path,
            dependencies_path=dependencies_path,
            description=description,
        )
        if not should_apply(apply, plan["action"]):
            return plan
        _apply_agent_definition(
            client,
            instance_id,
            workspace_key,
            plan,
            detail=detail,
            etag=etag,
        )
    with agent_clients(settings) as clients:
        instance_id, workspace_key, client = clients
        return get_agent_response(client, instance_id, workspace_key, agent_name)


def _agent_workspace_file_path(workspace_dir, relative_path, field_name):
    """Build one safe absolute agent file path from a relative folder path."""
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise AidpError(f"{field_name} must be a nonempty relative file path.")
    relative = PurePosixPath(relative_path)
    if (
        relative.is_absolute()
        or relative == PurePosixPath(".")
        or any(part in (".", "..") for part in relative.parts)
        or any(
            ord(character) < 32 or ord(character) == 127 for character in relative_path
        )
    ):
        raise AidpError(f"{field_name} must be a safe relative file path.")
    return str(PurePosixPath(workspace_dir) / relative)


def _require_agent_source_files(settings, entry_path, dependencies_path):
    """Read required source files before planning a definition mutation."""
    with workspace_clients(settings) as clients:
        instance_id, workspace_key, _, _, _, objects = clients
        for label, path in (
            ("entry_file", entry_path),
            ("dependencies_file", dependencies_path),
        ):
            if (
                path is not None
                and read_workspace_file(objects, instance_id, workspace_key, path)
                is None
            ):
                raise AidpError(f"{label} does not exist in the workspace: {path}.")


def _agent_definition_plan(
    client,
    instance_id,
    workspace_key,
    matches,
    *,
    agent_name,
    entry_path,
    dependencies_path,
    description,
):
    """Build a CODE-only definition plan from zero or one exact agent match."""
    if len(matches) > 1:
        raise AidpError(
            f"Agent name {agent_name!r} has {len(matches)} visible exact matches."
        )
    desired = {
        "entry_file_path": entry_path,
        "dependencies_file_path": dependencies_path,
        "description": description,
    }
    if not matches:
        return _new_agent_definition_plan(agent_name, desired), None, None
    response = client.get_agent(
        instance_id, workspace_key, resource_key(matches[0], "Agent")
    )
    detail = response.data
    if getattr(detail, "type", None) != "CODE":
        raise AidpError("Existing agent is not CODE and will not be converted.")
    current = _agent_definition_fields(detail)
    if description is None:
        desired["description"] = current["description"]
    changes = {
        field: value for field, value in desired.items() if current[field] != value
    }
    action = "update" if changes else "unchanged"
    return (
        _existing_agent_definition_plan(
            detail, current, desired, action, changes=changes
        ),
        detail,
        getattr(response, "headers", {}).get("etag"),
    )


def _new_agent_definition_plan(agent_name, desired):
    """Return the public creation plan without an existing agent payload."""
    return {
        "action": "create",
        "agent_name": agent_name,
        "current": {
            "entry_file_path": None,
            "dependencies_file_path": None,
            "description": None,
        },
        "desired": desired,
        "lifecycle_state": None,
        "deployment_mode": None,
        "redeploy_note": None,
    }


def _existing_agent_definition_plan(detail, current, desired, action, *, changes):
    """Return the public plan for a verified existing CODE agent."""
    deployment_mode = getattr(detail, "deployment_mode", None)
    return {
        "action": action,
        "agent_name": getattr(detail, "display_name", None),
        "current": current,
        "desired": desired,
        "changed_fields": sorted(changes),
        "lifecycle_state": getattr(detail, "lifecycle_state", None),
        "deployment_mode": deployment_mode,
        "redeploy_note": (
            "Changes take effect only after a redeploy, which this tool does "
            "not perform."
            if deployment_mode != "NOT_DEPLOYED"
            else None
        ),
    }


def _agent_definition_fields(agent):
    """Return exactly the fields that this definition tool may manage."""
    return {
        "entry_file_path": getattr(agent, "entry_file_path", None),
        "dependencies_file_path": getattr(agent, "dependencies_file_path", None),
        "description": getattr(agent, "description", None),
    }


def _apply_agent_definition(client, instance_id, workspace_key, plan, *, detail, etag):
    """Create or minimally update a CODE agent using the already-built plan."""
    if plan["action"] == "create":
        desired = plan["desired"]
        client.create_agent(
            instance_id,
            workspace_key,
            models.CreateAgentDetails(
                display_name=plan["agent_name"],
                type="CODE",
                path_info="/Workspace",
                entry_file_path=desired["entry_file_path"],
                dependencies_file_path=desired["dependencies_file_path"],
                description=desired["description"],
            ),
        )
        return
    if plan["action"] == "update":
        changes = {field: plan["desired"][field] for field in plan["changed_fields"]}
        request_kwargs = {"if_match": etag} if etag else {}
        client.update_agent(
            instance_id,
            workspace_key,
            resource_key(detail, "Agent"),
            models.UpdateAgentDetails(**changes),
            **request_kwargs,
        )


def _local_agent_upload_entries(directory, workspace_dir, local_files):
    """Read local files and construct private planning records in sorted order."""
    entries = []
    for file_path in local_files:
        relative_path = file_path.relative_to(directory).as_posix()
        try:
            data = file_path.read_bytes()
        except OSError as error:
            raise AidpError(
                f"Could not read local agent file {relative_path}."
            ) from error
        entries.append(
            {
                "relative_path": relative_path,
                "workspace_path": str(PurePosixPath(workspace_dir) / relative_path),
                "data": data,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "action": None,
            }
        )
    return entries


def _classify_agent_upload_entries(objects, instance_id, workspace_key, entries):
    """Classify each local file by comparing raw local and remote SHA-256 bytes."""
    for entry in entries:
        remote = read_workspace_file(
            objects, instance_id, workspace_key, entry["workspace_path"]
        )
        if remote is None:
            entry["action"] = "create"
        elif hashlib.sha256(remote).hexdigest() == entry["sha256"]:
            entry["action"] = "unchanged"
        else:
            entry["action"] = "update"


def _remote_only_agent_files(
    objects, instance_id, workspace_key, workspace_dir, entries
):
    """List bounded remote files in local-tree directories absent from local input."""
    local_paths = {entry["relative_path"] for entry in entries}
    local_directories = {"."}
    for relative_path in local_paths:
        parent = PurePosixPath(relative_path).parent
        while parent != PurePosixPath("."):
            local_directories.add(parent.as_posix())
            parent = parent.parent
    remote_only = []
    seen = set()
    for relative_directory in sorted(local_directories):
        directory = (
            workspace_dir
            if relative_directory == "."
            else str(PurePosixPath(workspace_dir) / relative_directory)
        )
        objects_in_directory, _ = list_workspace_objects(
            objects,
            instance_id,
            workspace_key,
            directory,
            MAX_REMOTE_ONLY_FILES,
        )
        for item in objects_in_directory:
            relative_path = _remote_relative_path(
                getattr(item, "path", None), workspace_dir
            )
            if (
                getattr(item, "type", None) == "FILE"
                and relative_path is not None
                and relative_path not in local_paths
                and relative_path not in seen
            ):
                remote_only.append(relative_path)
                seen.add(relative_path)
                if len(remote_only) == MAX_REMOTE_ONLY_FILES:
                    return remote_only
    return remote_only


def _remote_relative_path(path, workspace_dir):
    """Return one validated remote relative path that remains inside the target."""
    if not isinstance(path, str):
        return None
    candidate = PurePosixPath(path)
    root = PurePosixPath(workspace_dir)
    if not candidate.is_absolute() or any(
        part in (".", "..") for part in candidate.parts
    ):
        return None
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return None
    return None if relative == PurePosixPath(".") else relative.as_posix()


def _agent_upload_result(directory, workspace_dir, entries, remote_only):
    """Return the public, bounded and content-free portion of an upload plan."""
    counts = {action: 0 for action in ("create", "update", "unchanged")}
    for entry in entries:
        counts[entry["action"]] += 1
    return {
        "local_root": directory.name,
        "workspace_dir": workspace_dir,
        "counts": counts,
        "files": [
            {
                "relative_path": entry["relative_path"],
                "action": entry["action"],
                "size": entry["size"],
                "sha256": entry["sha256"][:12],
            }
            for entry in entries
        ],
        "remote_only": remote_only[:MAX_REMOTE_ONLY_FILES],
    }


def _agent_upload_action(entries):
    """Return a no-op action only when every file is already identical."""
    return (
        "unchanged"
        if all(entry["action"] == "unchanged" for entry in entries)
        else "upload"
    )


def _with_upload_counts(result, apply, uploaded, verified):
    """Attach apply and optional write-result counters without mutating a plan."""
    result["apply"] = apply
    if apply:
        result["uploaded"] = uploaded
        result["verified"] = verified
    return result


def _create_agent_upload_folders(
    objects, instance_id, workspace_key, workspace_dir, entries
):
    """Create all required folder paths in parent-first order, idempotently."""
    root = PurePosixPath(workspace_dir)
    folders = {root}
    for entry in entries:
        parent = PurePosixPath(entry["workspace_path"]).parent
        while parent != root.parent:
            folders.add(parent)
            if parent == root:
                break
            parent = parent.parent
    for folder in sorted(folders, key=lambda value: (len(value.parts), str(value))):
        create_workspace_folder(objects, instance_id, workspace_key, str(folder))


def _apply_agent_upload(objects, instance_id, workspace_key, entries):
    """Upload changed files in order and verify each raw-byte digest afterward."""
    uploaded = []
    verified = 0
    for entry in entries:
        if entry["action"] == "unchanged":
            continue
        try:
            upload_workspace_file(
                objects,
                instance_id,
                workspace_key,
                entry["workspace_path"],
                entry["data"],
                overwrite=entry["action"] == "update",
            )
            uploaded.append(entry["relative_path"])
            remote = read_workspace_file(
                objects, instance_id, workspace_key, entry["workspace_path"]
            )
            if remote is None or hashlib.sha256(remote).hexdigest() != entry["sha256"]:
                raise AidpError("read-back SHA-256 verification did not match.")
            verified += 1
        except Exception as error:
            completed = ", ".join(uploaded) or "none"
            raise AidpError(
                f"Agent code upload failed for {entry['relative_path']}; "
                f"uploaded files: {completed}."
            ) from error
    return len(uploaded), verified
