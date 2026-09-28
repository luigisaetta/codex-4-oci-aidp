"""
Author: L. Saetta
Date last modified: 2026-09-28
License: MIT
Description: Validate local Codex skill metadata and installer safety behavior.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOTS = (REPOSITORY_ROOT / "skills", REPOSITORY_ROOT / ".agents" / "skills")
INSTALL_SCRIPT = REPOSITORY_ROOT / "scripts" / "install_skills.sh"
TOOL_SNAPSHOT = REPOSITORY_ROOT / "aidp_mcp" / "tests" / "fixtures" / "mcp_tools.json"
ALLOWED_FRONTMATTER_KEYS = {
    "name",
    "description",
    "license",
    "allowed-tools",
    "metadata",
}
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MARKDOWN_LINK_PATTERN = re.compile(r"!?\[[^]]*\]\(([^)]+)\)")
BACKTICKED_IDENTIFIER_PATTERN = re.compile(r"`([a-z][a-z0-9_]*)`")
MCP_TOOL_PREFIXES = (
    "list_",
    "get_",
    "upload_",
    "ensure_",
    "deploy_",
    "invoke_",
    "start_",
    "set_",
    "find_",
)


def parse_frontmatter(skill_path: Path) -> dict[str, str]:
    """Parse simple YAML ``key: value`` frontmatter from one skill file.

    Args:
        skill_path: Path to the ``SKILL.md`` file to parse.

    Returns:
        Mapping of frontmatter keys to stripped values.

    Raises:
        AssertionError: If frontmatter is missing, malformed, or non-simple.
    """
    lines = skill_path.read_text(encoding="utf-8").splitlines()
    assert lines and lines[0] == "---", f"{skill_path} must start with frontmatter"
    try:
        closing_index = lines.index("---", 1)
    except ValueError as error:
        raise AssertionError(f"{skill_path} frontmatter is not closed") from error

    metadata = {}
    for line in lines[1:closing_index]:
        key, separator, value = line.partition(":")
        assert (
            separator and key and value.strip()
        ), f"{skill_path} has unsupported frontmatter line: {line!r}"
        metadata[key.strip()] = value.strip().strip('"')
    return metadata


def iter_skill_paths() -> list[Path]:
    """Return every repository skill definition in both configured roots."""
    return [
        skill_path
        for root in SKILL_ROOTS
        if root.exists()
        for skill_path in root.rglob("SKILL.md")
    ]


def assert_relative_links_exist(skill_path: Path) -> None:
    """Assert that each relative Markdown link in a skill resolves locally."""
    for target in MARKDOWN_LINK_PATTERN.findall(skill_path.read_text(encoding="utf-8")):
        target_path = target.split("#", maxsplit=1)[0]
        if not target_path or "://" in target_path or target_path.startswith("mailto:"):
            continue
        assert (
            skill_path.parent / target_path
        ).exists(), f"{skill_path} links to missing file {target_path!r}"


def referenced_mcp_tools(markdown_paths: list[Path]) -> set[str]:
    """Return backticked identifiers that match the documented MCP-tool shape.

    Args:
        markdown_paths: Markdown files belonging to one operational skill.

    Returns:
        Tool-like identifiers referenced by the supplied Markdown files.
    """
    identifiers = {
        identifier
        for path in markdown_paths
        for identifier in BACKTICKED_IDENTIFIER_PATTERN.findall(
            path.read_text(encoding="utf-8")
        )
    }
    return {
        identifier
        for identifier in identifiers
        if "aidp" in identifier or identifier.startswith(MCP_TOOL_PREFIXES)
    }


def test_skill_metadata_and_links() -> None:
    """Validate frontmatter, naming, descriptions, and local links for skills."""
    for skill_path in iter_skill_paths():
        metadata = parse_frontmatter(skill_path)
        assert set(metadata).issubset(ALLOWED_FRONTMATTER_KEYS)
        assert metadata.get("name") == skill_path.parent.name
        assert SKILL_NAME_PATTERN.fullmatch(metadata["name"])
        assert metadata.get("description")
        assert len(metadata["description"]) <= 500
        assert_relative_links_exist(skill_path)


def test_operational_skills_reference_snapshot_tools() -> None:
    """Ensure skill tool references match the MCP snapshot and each contract."""
    available_tools = {
        tool["name"] for tool in json.loads(TOOL_SNAPSHOT.read_text(encoding="utf-8"))
    }
    expected_tools_by_skill = {
        "aidp-notebook-deploy-and-run": {
            "upload_notebook",
            "find_notebook_jobs",
            "ensure_notebook_job",
            "get_cluster_status",
            "set_cluster_state",
            "start_notebook_job",
            "get_job_run_output",
        },
        "aidp-agent-deploy": {
            "upload_aidp_agent_code",
            "ensure_aidp_agent",
            "get_cluster_status",
            "set_cluster_state",
            "deploy_aidp_agent",
            "invoke_aidp_agent",
            "list_aidp_async_operations",
        },
    }
    all_markdown_paths = list((REPOSITORY_ROOT / "skills").rglob("*.md"))
    assert referenced_mcp_tools(all_markdown_paths).issubset(available_tools)
    skill_paths = list((REPOSITORY_ROOT / "skills").rglob("SKILL.md"))
    assert {path.parent.name for path in skill_paths} == set(expected_tools_by_skill)
    for skill_path in skill_paths:
        markdown_paths = list(skill_path.parent.rglob("*.md"))
        referenced_tools = referenced_mcp_tools(markdown_paths)
        expected_tools = expected_tools_by_skill[skill_path.parent.name]
        assert expected_tools.issubset(referenced_tools)
        assert expected_tools.issubset(available_tools)
        assert referenced_tools.issubset(available_tools)


def run_install_script(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Run the installer and return captured output for assertions.

    Args:
        *arguments: Command-line arguments passed to the installer.

    Returns:
        Completed process result with captured standard output and error.
    """
    return subprocess.run(
        [str(INSTALL_SCRIPT), *arguments],
        check=False,
        text=True,
        capture_output=True,
    )


def test_install_script_with_temporary_target(tmp_path: Path) -> None:
    """Check dry-run, idempotence, conflicts, and scoped uninstall behavior."""
    source_skill = REPOSITORY_ROOT / "skills" / "aidp-install-script-test"
    target_dir = tmp_path / "user-skills"
    target_skill = target_dir / source_skill.name
    unrelated_link = target_dir / "unrelated-skill"
    source_skill.mkdir()
    (source_skill / "SKILL.md").write_text(
        "---\n"
        "name: aidp-install-script-test\n"
        "description: Temporary installer test skill.\n"
        "---\n",
        encoding="utf-8",
    )
    try:
        dry_run = run_install_script("--dry-run", "--target", str(target_dir))
        assert dry_run.returncode == 0
        assert "would create target directory" in dry_run.stdout
        assert not target_dir.exists()

        first_run = run_install_script("--target", str(target_dir))
        assert first_run.returncode == 0
        assert "created: aidp-install-script-test" in first_run.stdout
        assert target_skill.is_symlink()
        assert target_skill.resolve() == source_skill

        second_run = run_install_script("--target", str(target_dir))
        assert second_run.returncode == 0
        assert "unchanged: aidp-install-script-test" in second_run.stdout

        target_skill.unlink()
        target_skill.mkdir()
        conflict_run = run_install_script("--target", str(target_dir))
        assert conflict_run.returncode == 1
        assert "conflict: aidp-install-script-test" in conflict_run.stdout
        assert target_skill.is_dir() and not target_skill.is_symlink()

        target_skill.rmdir()
        restored_run = run_install_script("--target", str(target_dir))
        assert restored_run.returncode == 0
        assert target_skill.is_symlink()
        unrelated_link.symlink_to(tmp_path / "unrelated-source")

        uninstall_run = run_install_script("--uninstall", "--target", str(target_dir))
        assert uninstall_run.returncode == 0
        assert "removed: aidp-install-script-test" in uninstall_run.stdout
        assert not target_skill.exists()
        assert unrelated_link.is_symlink()
    finally:
        shutil.rmtree(source_skill)
