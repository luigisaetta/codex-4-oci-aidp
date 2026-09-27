"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Pure MCP remote-path, name, encoding, and result-limit validation.
"""

from pathlib import PurePosixPath
from urllib.parse import quote

from aidp_common.connection import AidpError


def validate_workspace_path(workspace_path):
    """Validate an SDK workspace-relative notebook path without traversal.

    Args:
        workspace_path: POSIX path relative to the AI DP workspace root.

    Returns:
        str: Normalized workspace path.

    Raises:
        AidpError: The path is not a relative notebook path.
    """
    path = PurePosixPath(workspace_path)
    if (
        path.is_absolute()
        or path.suffix != ".ipynb"
        or ".." in path.parts
        or path == PurePosixPath(".")
    ):
        raise AidpError("Workspace path must be a relative .ipynb path.")
    return str(path)


def workspace_content_path(workspace_path):
    """Return the notebook service's absolute path for a relative input.

    Args:
        workspace_path: Valid path relative to the workspace root.

    Returns:
        str: Absolute notebook service path beneath `/Workspace`.
    """
    return f"/Workspace/{workspace_path}"


def validate_workspace_directory(path):
    """Validate an absolute AI DP workspace directory path.

    Args:
        path: Absolute directory path rooted at ``/Workspace``.

    Returns:
        str: Normalized absolute workspace directory path.

    Raises:
        AidpError: The path is not a safe workspace directory.
    """
    if not isinstance(path, str) or not path.strip():
        raise AidpError("Workspace directory path must be a nonempty string.")
    candidate = PurePosixPath(path)
    if not candidate.is_absolute() or candidate.parts[1:2] != ("Workspace",):
        raise AidpError("Workspace directory path must be rooted at /Workspace.")
    if any(part in (".", "..") for part in candidate.parts):
        raise AidpError("Workspace directory path must not contain traversal.")
    return str(candidate)


def validate_workspace_notebook_path(path):
    """Validate an absolute notebook path used for workspace discovery.

    Args:
        path: Absolute notebook path rooted at ``/Workspace``.

    Returns:
        str: Normalized absolute notebook path.

    Raises:
        AidpError: The path is not a safe workspace notebook path.
    """
    candidate = PurePosixPath(validate_workspace_directory(path))
    if candidate.suffix != ".ipynb":
        raise AidpError("Workspace notebook path must end in .ipynb.")
    return str(candidate)


def validate_resource_name(value, label):
    """Validate an exact AI DP display-name selector.

    Args:
        value: Candidate catalog, schema, or volume display name.
        label: Human-readable resource type for the error message.

    Returns:
        str: The unchanged, nonempty display name.

    Raises:
        AidpError: The selector is not a supported display name.
    """
    if not isinstance(value, str) or not value.strip() or len(value) > 255:
        raise AidpError(f"{label} name must be a nonempty string up to 255 characters.")
    return value


def validate_volume_path(path):
    """Validate one absolute, traversal-free path within a volume.

    Args:
        path: Absolute POSIX path rooted at the volume root.

    Returns:
        str: Normalized POSIX path.

    Raises:
        AidpError: The path is empty, relative, or contains traversal.
    """
    if not isinstance(path, str) or not path.strip():
        raise AidpError("Volume path must be a nonempty absolute POSIX path.")
    candidate = PurePosixPath(path)
    if not candidate.is_absolute() or any(
        part in (".", "..") for part in candidate.parts
    ):
        raise AidpError("Volume path must be absolute and must not contain traversal.")
    return str(candidate)


def _normalized_task_notebook_path(path):
    """Normalize a remote notebook-task path for a safe equality comparison."""
    if not isinstance(path, str) or not path:
        return None
    candidate = PurePosixPath(path)
    try:
        if candidate.is_absolute():
            return validate_workspace_notebook_path(path)
        return workspace_content_path(validate_workspace_path(path))
    except AidpError:
        return None


def encoded_content_path(content_path):
    """Encode an absolute notebook path for the SDK URL path parameter.

    Args:
        content_path: Absolute path returned or accepted by the notebook service.

    Returns:
        str: URL-path-safe encoded content path.
    """
    return quote(content_path, safe="")


def _validate_result_limit(value, maximum):
    """Validate a bounded MCP collection result limit."""
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        raise AidpError(f"max_results must be an integer from 1 through {maximum}.")
