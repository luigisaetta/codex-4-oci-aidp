"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Local upload-root policy and safe notebook-file validation.
"""

import hashlib
import json
import os
from pathlib import Path

from aidp_common.connection import AidpError

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def allowed_local_roots(value):
    """Resolve and validate the operator-configured upload roots.

    Args:
        value: Colon- or platform-path-separator-delimited directory list.

    Returns:
        tuple[Path, ...]: Canonical directories from which uploads are allowed.

    Raises:
        AidpError: A configured root is missing, not a directory, or too broad.
    """
    if not value:
        return (PROJECT_ROOT,)
    home = Path.home().resolve()
    roots = []
    for entry in value.split(os.pathsep):
        if not entry:
            continue
        root = Path(entry).expanduser().resolve()
        filesystem_root = Path(root.anchor)
        if (
            not root.is_dir()
            or root == filesystem_root
            or root == home
            or home.is_relative_to(root)
        ):
            raise AidpError("AIDP_ALLOWED_ROOTS contains an invalid allowed root.")
        roots.append(root)
    return tuple(roots) or (PROJECT_ROOT,)


def validate_local_path(local_path, roots):
    """Resolve one candidate and confirm it is contained in an allowed root.

    Args:
        local_path: Candidate file path supplied to an MCP tool.
        roots: Canonical allowed directories selected by the operator.

    Returns:
        tuple[Path, Path]: Canonical candidate path and its matching root.

    Raises:
        AidpError: The candidate is relative with multiple roots or is outside
            every allowed root.
    """
    supplied_path = Path(local_path).expanduser()
    if not supplied_path.is_absolute() and (
        len(roots) != 1 or roots[0] != PROJECT_ROOT
    ):
        raise AidpError(
            "Local notebook path must be absolute when AIDP_ALLOWED_ROOTS "
            "is configured."
        )
    candidate = supplied_path.resolve()
    for root in roots:
        try:
            candidate.relative_to(root)
            return candidate, root
        except ValueError:
            continue
    raise AidpError(
        "Local notebook path must be inside an allowed root; see AIDP_ALLOWED_ROOTS."
    )


def validate_local_notebook(local_path, roots=None):
    """Read a local notebook safely and return its parsed JSON and digest.

    Args:
        local_path: Local notebook path within an allowed root.
        roots: Optional canonical allowed roots; defaults to the project root.

    Returns:
        tuple[Path, Path, dict, str]: Canonical path, matching allowed root,
        parsed notebook JSON, and SHA-256.

    Raises:
        AidpError: The path is unsafe, absent, not a notebook, or invalid JSON.
    """
    candidate, matching_root = validate_local_path(local_path, roots or (PROJECT_ROOT,))
    if candidate.suffix != ".ipynb" or not candidate.is_file():
        raise AidpError("Local notebook must be an existing .ipynb file.")
    try:
        payload = candidate.read_bytes()
        content = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AidpError("Local notebook must contain valid UTF-8 JSON.") from exc
    if not isinstance(content, dict) or not content:
        raise AidpError("Local notebook JSON must be a nonempty object.")
    return candidate, matching_root, content, hashlib.sha256(payload).hexdigest()
