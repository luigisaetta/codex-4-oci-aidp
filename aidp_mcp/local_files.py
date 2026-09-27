"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Local upload-root policy and safe notebook and agent-file validation.
"""

import hashlib
import json
import os
from pathlib import Path

from aidp_common.connection import AidpError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_AGENT_FILES = 500
MAX_AGENT_FILE_BYTES = 500 * 1024 * 1024
MAX_AGENT_TOTAL_BYTES = 5 * 1024 * 1024 * 1024
_SKIPPED_AGENT_DIRECTORIES = {"__pycache__", ".pytest_cache", ".git", ".venv"}
_SKIPPED_AGENT_FILENAMES = {".DS_Store"}


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


def validate_local_directory(local_dir, roots):
    """Resolve and validate one local agent directory under an allowed root.

    Args:
        local_dir: Candidate directory supplied to an MCP tool.
        roots: Canonical allowed directories selected by the operator.

    Returns:
        tuple[Path, Path]: Canonical directory and its matching allowed root.

    Raises:
        AidpError: The directory is relative when it must be absolute, outside
            configured roots, or does not exist.
    """
    supplied_path = Path(local_dir).expanduser()
    if not supplied_path.is_absolute() and (
        len(roots) != 1 or roots[0] != PROJECT_ROOT
    ):
        raise AidpError(
            "Local agent directory must be absolute when AIDP_ALLOWED_ROOTS "
            "is configured."
        )
    directory = supplied_path.resolve()
    for root in roots:
        try:
            directory.relative_to(root)
            if directory.is_dir():
                return directory, root
            raise AidpError("Local agent directory must be an existing directory.")
        except ValueError:
            continue
    raise AidpError(
        "Local agent directory must be inside an allowed root; see AIDP_ALLOWED_ROOTS."
    )


def collect_agent_files(directory):
    """Collect safe agent-upload files in deterministic relative POSIX order.

    Build artifacts are skipped. Secret-like files and symbolic links are
    refused rather than skipped so operators remove them deliberately.

    Args:
        directory: Existing resolved local agent directory.

    Returns:
        list[Path]: Uploadable regular files sorted by relative POSIX path.

    Raises:
        AidpError: A secret, symbolic link, empty directory, or documented
            upload limit is encountered.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise AidpError("Local agent directory must be an existing directory.")
    refused = []
    files = []
    for current_root, directories, filenames in os.walk(directory, followlinks=False):
        current = Path(current_root)
        for name in sorted(directories):
            candidate = current / name
            relative = candidate.relative_to(directory).as_posix()
            if candidate.is_symlink() or name == ".oci":
                refused.append(relative)
        directories[:] = [
            name for name in directories if name not in _SKIPPED_AGENT_DIRECTORIES
        ]
        for name in sorted(filenames):
            candidate = current / name
            relative = candidate.relative_to(directory).as_posix()
            if (
                candidate.is_symlink()
                or _is_refused_agent_file(name)
                or ".oci" in candidate.relative_to(directory).parts
            ):
                refused.append(relative)
            elif not _is_skipped_agent_file(name):
                files.append(candidate)
    if refused:
        raise AidpError(
            "Agent upload refuses secret files or symbolic links: "
            + ", ".join(sorted(refused))
        )
    files.sort(key=lambda candidate: candidate.relative_to(directory).as_posix())
    if not files:
        raise AidpError("Local agent directory has no uploadable files.")
    _validate_agent_file_limits(files, directory)
    return files


def _is_refused_agent_file(name):
    """Return whether a file name matches a secret-refusal policy."""
    return (
        name == ".env"
        or name.startswith(".env.")
        or name.endswith((".pem", ".key", ".p12"))
        or name.startswith("id_rsa")
    )


def _is_skipped_agent_file(name):
    """Return whether a file name is a silent build or tool artifact."""
    return name in _SKIPPED_AGENT_FILENAMES or name.endswith(".pyc")


def _validate_agent_file_limits(files, directory):
    """Enforce documented agent file-count and byte limits with actual values."""
    count = len(files)
    if count > MAX_AGENT_FILES:
        raise AidpError(
            f"Agent upload has {count} files; maximum is {MAX_AGENT_FILES}."
        )
    total_bytes = 0
    for file_path in files:
        size = file_path.stat().st_size
        relative = file_path.relative_to(directory).as_posix()
        if size > MAX_AGENT_FILE_BYTES:
            raise AidpError(
                f"Agent upload file {relative} has {size} bytes; maximum is "
                f"{MAX_AGENT_FILE_BYTES}."
            )
        total_bytes += size
    if total_bytes > MAX_AGENT_TOTAL_BYTES:
        raise AidpError(
            f"Agent upload has {count} files totaling {total_bytes} bytes; maximum is "
            f"{MAX_AGENT_TOTAL_BYTES}."
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
