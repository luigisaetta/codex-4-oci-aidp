"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Offline tests for MCP local upload-root validation.
"""

import json

import pytest

from aidp_common.connection import AidpError
from aidp_mcp import local_files


def test_validate_local_notebook_returns_json_and_digest(tmp_path, monkeypatch):
    """A repository-local valid notebook returns parsed content and a digest."""
    monkeypatch.setattr(local_files, "PROJECT_ROOT", tmp_path)
    notebook = tmp_path / "example.ipynb"
    notebook.write_text(json.dumps({"cells": [], "nbformat": 4}), encoding="utf-8")

    path, root, content, digest = local_files.validate_local_notebook(notebook)

    assert path == notebook
    assert root == tmp_path
    assert content["nbformat"] == 4
    assert len(digest) == 64


def test_validate_local_notebook_rejects_path_outside_repository(tmp_path, monkeypatch):
    """The MCP adapter cannot upload arbitrary local paths."""
    monkeypatch.setattr(local_files, "PROJECT_ROOT", tmp_path / "repository")
    outside = tmp_path / "outside.ipynb"
    outside.write_text("{}", encoding="utf-8")

    with pytest.raises(AidpError, match="AIDP_ALLOWED_ROOTS"):
        local_files.validate_local_notebook(outside)


def test_allowed_roots_default_to_project_root(tmp_path, monkeypatch):
    """An absent allowed-roots setting retains the repository-only boundary."""
    monkeypatch.setattr(local_files, "PROJECT_ROOT", tmp_path)

    assert local_files.allowed_local_roots("") == (tmp_path,)


def test_validate_local_notebook_accepts_second_configured_root(tmp_path):
    """A notebook under any configured root is accepted with its matching root."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    notebook = second / "nested" / "example.ipynb"
    notebook.parent.mkdir()
    notebook.write_text('{"nbformat": 4}', encoding="utf-8")
    roots = local_files.allowed_local_roots(f"{first}{local_files.os.pathsep}{second}")

    path, matching_root, content, _ = local_files.validate_local_notebook(
        notebook, roots
    )

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
        local_files.validate_local_notebook(relative_path, (server_root, extra_root))


def test_validate_local_notebook_rejects_relative_path_with_one_external_root(
    tmp_path, monkeypatch
):
    """An explicitly configured non-repository root cannot accept a relative path."""
    repository_root = tmp_path / "server-repository"
    external_root = tmp_path / "other-project"
    repository_root.mkdir()
    external_root.mkdir()
    monkeypatch.chdir(repository_root)
    monkeypatch.setattr(local_files, "PROJECT_ROOT", repository_root)

    with pytest.raises(AidpError, match="must be absolute"):
        local_files.validate_local_notebook("notebooks/example.ipynb", (external_root,))


def test_validate_local_notebook_accepts_relative_path_with_default_root(
    tmp_path, monkeypatch
):
    """The default repository root preserves its documented relative-path mode."""
    notebook = tmp_path / "notebooks" / "example.ipynb"
    notebook.parent.mkdir()
    notebook.write_text('{"nbformat": 4}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(local_files, "PROJECT_ROOT", tmp_path)

    path, root, _, _ = local_files.validate_local_notebook("notebooks/example.ipynb")

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
        local_files.validate_local_notebook(linked, (root,))


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
    monkeypatch.setattr(local_files.Path, "home", classmethod(lambda _cls: home))
    values = {
        "filesystem": "/",
        "home": str(home),
        "ancestor": str(sandbox),
        "missing": str(sandbox / "missing"),
        "file": str(regular_file),
    }

    with pytest.raises(AidpError) as error:
        local_files.allowed_local_roots(values[kind])

    assert "AIDP_ALLOWED_ROOTS" in str(error.value)
    assert values[kind] not in str(error.value)
