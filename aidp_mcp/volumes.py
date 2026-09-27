"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: AI DP catalog-volume discovery and bounded volume file-tree operations.
"""

from pathlib import PurePosixPath

import oci

from aidp_common.connection import AidpError
from aidp_mcp.lookups import (
    SDK_PAGE_SIZE,
    _sorted_named_resources,
    next_page,
    resource_key,
)
from aidp_mcp.targets import catalog_clients
from aidp_mcp.validation import (
    _validate_result_limit,
    validate_resource_name,
    validate_volume_path,
)

MAX_CATALOG_VOLUME_RESULTS = 1000
MAX_VOLUME_FILE_RESULTS = 1000


def list_catalog_volumes(settings, catalog_name, external_only=True, max_results=100):
    """List visible volumes below one exact catalog name.

    Args:
        catalog_name: Exact, case-sensitive catalog display name.
        external_only: When true, return only external Object Storage volumes.
        max_results: Maximum returned volumes across all schemas.

    Returns:
        dict: Sanitized schema-to-volume hierarchy and truncation state.

    Raises:
        AidpError: The selection is invalid, ambiguous, or inaccessible.
    """
    catalog_name = validate_resource_name(catalog_name, "Catalog")
    if not isinstance(external_only, bool):
        raise AidpError("external_only must be a boolean.")
    _validate_result_limit(max_results, MAX_CATALOG_VOLUME_RESULTS)
    schema_nodes = []
    matched_count = 0
    is_truncated = False
    with catalog_clients(settings) as clients:
        instance_id, catalogs, schemas, volume_client = clients
        catalog = _find_exact_catalog(catalogs, instance_id, catalog_name)
        schema_items = oci.pagination.list_call_get_all_results(
            schemas.list_schemas, instance_id, resource_key(catalog, "Catalog")
        ).data
        for schema in _sorted_named_resources(schema_items, "Schema"):
            volume_items = oci.pagination.list_call_get_all_results(
                volume_client.list_volumes,
                instance_id,
                resource_key(catalog, "Catalog"),
                resource_key(schema, "Schema"),
            ).data
            node_volumes = []
            for summary in _sorted_named_resources(volume_items, "Volume"):
                detail = volume_client.get_volume(
                    instance_id, resource_key(summary, "Volume")
                ).data
                volume_type = getattr(detail, "volume_type", None)
                if external_only and volume_type != "EXTERNAL":
                    continue
                if matched_count == max_results:
                    is_truncated = True
                    break
                node_volumes.append(_volume_summary(detail))
                matched_count += 1
            if node_volumes:
                schema_nodes.append(
                    {
                        "display_name": getattr(schema, "display_name", None),
                        "volumes": node_volumes,
                    }
                )
            if is_truncated:
                break
    return {
        "catalog_name": getattr(catalog, "display_name", None),
        "external_only": external_only,
        "schemas": schema_nodes,
        "is_truncated": is_truncated,
    }


def list_volume_files(
    settings, catalog_name, schema_name, volume_name, *, path="/", max_results=100
):
    """List a bounded recursive folder and file tree in one volume.

    Args:
        catalog_name: Exact, case-sensitive catalog display name.
        schema_name: Exact, case-sensitive schema display name.
        volume_name: Exact, case-sensitive volume display name.
        path: Absolute volume path from which to recursively list entries.
        max_results: Maximum returned files and folders.

    Returns:
        dict: Sanitized recursive hierarchy and truncation state.

    Raises:
        AidpError: The selection/path is invalid, ambiguous, or inaccessible.
    """
    catalog_name = validate_resource_name(catalog_name, "Catalog")
    schema_name = validate_resource_name(schema_name, "Schema")
    volume_name = validate_resource_name(volume_name, "Volume")
    path = validate_volume_path(path)
    _validate_result_limit(max_results, MAX_VOLUME_FILE_RESULTS)
    with catalog_clients(settings) as clients:
        instance_id, catalogs, schemas, volume_client = clients
        catalog = _find_exact_catalog(catalogs, instance_id, catalog_name)
        schema = _find_exact_schema(
            schemas, instance_id, resource_key(catalog, "Catalog"), schema_name
        )
        volume = _find_exact_volume(
            volume_client,
            instance_id,
            resource_key(catalog, "Catalog"),
            resource_key(schema, "Schema"),
            volume_name,
        )
        volume_root = _volume_mount_path(catalog, schema, volume)
        entries_by_path, is_truncated = _list_volume_file_entries(
            volume_client,
            instance_id=instance_id,
            volume_key=resource_key(volume, "Volume"),
            root_path=path,
            max_results=max_results,
            volume_root=volume_root,
        )
    return {
        "volume": {
            "catalog_name": getattr(catalog, "display_name", None),
            "schema_name": getattr(schema, "display_name", None),
            "display_name": getattr(volume, "display_name", None),
            "volume_key": resource_key(volume, "Volume"),
        },
        "path": path,
        "root": _volume_file_tree(path, list(entries_by_path.values())),
        "is_truncated": is_truncated,
    }


def _find_exact_catalog(catalogs, instance_id, catalog_name):
    """Resolve exactly one visible catalog by its display name."""
    items = oci.pagination.list_call_get_all_results(
        catalogs.list_catalogs, instance_id, display_name=catalog_name
    ).data
    matches = [
        item
        for item in _sorted_named_resources(items, "Catalog")
        if item.display_name == catalog_name
    ]
    if len(matches) != 1:
        raise AidpError(
            f"Catalog name has {len(matches)} visible exact matches; select a unique "
            "catalog."
        )
    return matches[0]


def _find_exact_schema(schemas, instance_id, catalog_key, schema_name):
    """Resolve exactly one schema in a selected catalog."""
    items = oci.pagination.list_call_get_all_results(
        schemas.list_schemas, instance_id, catalog_key, display_name=schema_name
    ).data
    matches = [
        item
        for item in _sorted_named_resources(items, "Schema")
        if item.display_name == schema_name
    ]
    if len(matches) != 1:
        raise AidpError(
            f"Schema name has {len(matches)} visible exact matches in the catalog."
        )
    return matches[0]


def _find_exact_volume(
    volume_client, instance_id, catalog_key, schema_key, volume_name
):
    """Resolve exactly one volume in a selected schema."""
    items = oci.pagination.list_call_get_all_results(
        volume_client.list_volumes,
        instance_id,
        catalog_key,
        schema_key,
        display_name=volume_name,
    ).data
    matches = [
        item
        for item in _sorted_named_resources(items, "Volume")
        if item.display_name == volume_name
    ]
    if len(matches) != 1:
        raise AidpError(
            f"Volume name has {len(matches)} visible exact matches in the schema."
        )
    return matches[0]


def _volume_summary(volume):
    """Return non-sensitive volume metadata needed for exploration."""
    volume_type = getattr(volume, "volume_type", None)
    return {
        "display_name": getattr(volume, "display_name", None),
        "volume_key": resource_key(volume, "Volume"),
        "full_name": getattr(volume, "full_name", None),
        "volume_type": volume_type,
        "storage_location": (
            getattr(volume, "storage_location", None)
            if volume_type == "EXTERNAL"
            else None
        ),
        "lifecycle_state": getattr(volume, "lifecycle_state", None),
    }


def _volume_mount_path(catalog, schema, volume):
    """Build the AI DP mount path for one resolved volume."""
    labels = (
        getattr(catalog, "display_name", None),
        getattr(schema, "display_name", None),
        getattr(volume, "display_name", None),
    )
    if any(
        not isinstance(label, str)
        or not label
        or PurePosixPath(label).parts != (label,)
        for label in labels
    ):
        raise AidpError("Resolved volume names cannot form a safe mount path.")
    return str(PurePosixPath("/Volumes").joinpath(*labels))


def _volume_file_summary(item, logical_root, volume_root):
    """Sanitize one item and normalize an optional AI DP mount-path prefix."""
    path = getattr(item, "path", None)
    if not isinstance(path, str):
        raise AidpError("Volume file response is missing its path.")
    normalized_path = validate_volume_path(path)
    candidate = PurePosixPath(normalized_path)
    mount_root = PurePosixPath(volume_root)
    try:
        candidate = PurePosixPath("/").joinpath(
            *candidate.relative_to(mount_root).parts
        )
    except ValueError:
        # Some AI DP responses already use a path relative to the volume.
        pass
    root = PurePosixPath(logical_root)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise AidpError("Volume file response is outside the requested path.") from exc
    entry_type = getattr(item, "type", None)
    if entry_type not in ("FILE", "FOLDER"):
        raise AidpError("Volume file response has an unsupported item type.")
    display_name = getattr(item, "display_name", None)
    if not isinstance(display_name, str) or not display_name:
        raise AidpError("Volume file response is missing its display name.")
    return {
        "display_name": display_name,
        "path": str(candidate),
        "type": entry_type,
        "time_created": getattr(item, "time_created", None),
        "time_updated": getattr(item, "time_updated", None),
    }


def _volume_file_tree(root_path, entries):
    """Build an ordered hierarchy from sanitized recursive file entries."""
    root = {
        "display_name": PurePosixPath(root_path).name or "/",
        "path": root_path,
        "type": "FOLDER",
        "children": {},
    }
    root_posix = PurePosixPath(root_path)
    for entry in entries:
        relative_parts = PurePosixPath(entry["path"]).relative_to(root_posix).parts
        if not relative_parts:
            continue
        node = root
        parent_parts = relative_parts[:-1]
        for index, part in enumerate(parent_parts):
            parent_path = str(root_posix.joinpath(*relative_parts[: index + 1]))
            child = node["children"].setdefault(
                part,
                {
                    "display_name": part,
                    "path": parent_path,
                    "type": "FOLDER",
                    "inferred": True,
                    "children": {},
                },
            )
            node = child
        leaf_name = relative_parts[-1]
        current = node["children"].get(leaf_name)
        if current and current.get("inferred") and entry["type"] == "FOLDER":
            current.update(entry)
            current.pop("inferred", None)
        else:
            node["children"][leaf_name] = {**entry, "children": {}}

    def render(node):
        children = node.pop("children")
        if node["type"] == "FOLDER":
            ordered = sorted(
                children.values(),
                key=lambda child: (
                    child["type"] != "FOLDER",
                    child["display_name"].casefold(),
                    child["display_name"],
                ),
            )
            node["children"] = [render(child) for child in ordered]
        return node

    return render(root)


def _list_volume_file_entries(
    volume_client, *, instance_id, volume_key, root_path, max_results, volume_root
):
    """Collect a bounded tree, compensating for shallow recursive responses."""
    entries_by_path = {}
    pending_paths = [root_path]
    inspected_paths = set()
    is_truncated = False
    while pending_paths and len(entries_by_path) < max_results:
        current_path = pending_paths.pop(0)
        if current_path in inspected_paths:
            continue
        inspected_paths.add(current_path)
        page = None
        response_entries = []
        while len(entries_by_path) < max_results:
            response = volume_client.list_files(
                instance_id,
                volume_key,
                current_path,
                is_recursive=True,
                limit=min(SDK_PAGE_SIZE, max_results - len(entries_by_path)),
                page=page,
                sort_by="displayName",
                sort_order="ASC",
            )
            items = getattr(response.data, "items", None) or []
            for index, item in enumerate(items):
                entry = _volume_file_summary(item, current_path, volume_root)
                response_entries.append(entry)
                entries_by_path.setdefault(entry["path"], entry)
                if len(entries_by_path) == max_results:
                    is_truncated = index < len(items) - 1
                    break
            page = next_page(response)
            if is_truncated or not page:
                break
        is_truncated = is_truncated or bool(page)
        if is_truncated:
            break
        _queue_unexpanded_volume_folders(
            pending_paths, inspected_paths, current_path, response_entries
        )
    return entries_by_path, is_truncated or bool(pending_paths)


def _queue_unexpanded_volume_folders(
    pending_paths, inspected_paths, current_path, entries
):
    """Queue folders without returned descendants for explicit inspection.

    AI DP can return only direct children despite accepting ``is_recursive``.
    Folders with no returned descendant are therefore inspected separately;
    folders already represented by a recursive response are not queried again.
    """
    current = PurePosixPath(current_path)
    folder_paths = {
        entry["path"]
        for entry in entries
        if entry["type"] == "FOLDER" and entry["path"] != current_path
    }
    for folder_path in sorted(folder_paths):
        folder = PurePosixPath(folder_path)
        has_descendant = any(
            entry["path"] != folder_path
            and _is_volume_path_descendant(PurePosixPath(entry["path"]), folder)
            for entry in entries
        )
        if (
            not has_descendant
            and folder_path not in inspected_paths
            and folder_path not in pending_paths
            and _is_volume_path_descendant(folder, current)
        ):
            pending_paths.append(folder_path)


def _is_volume_path_descendant(candidate, parent):
    """Return whether ``candidate`` is strictly below the POSIX ``parent``."""
    try:
        return candidate.relative_to(parent) != PurePosixPath(".")
    except ValueError:
        return False
