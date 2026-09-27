#!/usr/bin/env bash
# Install this repository's operational AI DP skills as user-scope symlinks.
# Prerequisites: Bash 3.2+ and a writable target directory.
# Inputs: --dry-run, --uninstall, and optional --target DIR.
# Side effects: creates or removes only symlinks pointing into ../skills.
# Usage: scripts/install_skills.sh [--dry-run] [--uninstall] [--target DIR]
#
# The default is ~/.agents/skills, empirically verified on 2026-09-27 with
# codex-cli 0.155.0-alpha.16.3. Start a new Codex session after changes.

set -euo pipefail

dry_run=false
uninstall=false
target_dir="${HOME}/.agents/skills"
script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
repository_root="$(CDPATH= cd -- "${script_dir}/.." && pwd)"
skills_dir="${repository_root}/skills"
had_conflict=false

usage() {
    echo "Usage: scripts/install_skills.sh [--dry-run] [--uninstall] [--target DIR]"
}

report_action() {
    local action="$1"
    local skill_name="$2"
    echo "${action}: ${skill_name}"
}

link_points_to_source() {
    local link_path="$1"
    local source_path="$2"
    local resolved_link

    resolved_link="$(CDPATH= cd -- "${link_path}" 2>/dev/null && pwd -P)" || return 1
    [[ "${resolved_link}" == "${source_path}" ]]
}

ensure_target_directory() {
    if [[ -d "${target_dir}" ]]; then
        return
    fi

    if [[ -e "${target_dir}" || -L "${target_dir}" ]]; then
        echo "conflict: target directory is not a directory: ${target_dir}" >&2
        exit 1
    fi

    if [[ "${dry_run}" == true ]]; then
        echo "would create target directory: ${target_dir}"
        return
    fi

    mkdir -p -- "${target_dir}"
    echo "created target directory: ${target_dir}"
}

install_skill() {
    local source_path="$1"
    local skill_name target_path

    skill_name="$(basename -- "${source_path}")"
    target_path="${target_dir}/${skill_name}"

    if [[ -L "${target_path}" ]]; then
        if link_points_to_source "${target_path}" "${source_path}"; then
            report_action "unchanged" "${skill_name}"
        else
            report_action "conflict" "${skill_name}"
            had_conflict=true
        fi
        return
    fi

    if [[ -e "${target_path}" ]]; then
        report_action "conflict" "${skill_name}"
        had_conflict=true
        return
    fi

    if [[ "${dry_run}" == true ]]; then
        report_action "would create" "${skill_name}"
        return
    fi

    ln -s -- "${source_path}" "${target_path}"
    report_action "created" "${skill_name}"
}

uninstall_skill() {
    local source_path="$1"
    local skill_name target_path

    skill_name="$(basename -- "${source_path}")"
    target_path="${target_dir}/${skill_name}"

    if [[ ! -L "${target_path}" ]]; then
        return
    fi

    if ! link_points_to_source "${target_path}" "${source_path}"; then
        report_action "conflict" "${skill_name}"
        had_conflict=true
        return
    fi

    if [[ "${dry_run}" == true ]]; then
        report_action "would remove" "${skill_name}"
        return
    fi

    rm -- "${target_path}"
    report_action "removed" "${skill_name}"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)
            dry_run=true
            ;;
        --uninstall)
            uninstall=true
            ;;
        --target)
            if [[ $# -lt 2 ]]; then
                echo "error: --target requires a directory." >&2
                usage >&2
                exit 2
            fi
            target_dir="$2"
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "error: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

if [[ ! -d "${skills_dir}" ]]; then
    echo "error: skills source directory does not exist: ${skills_dir}" >&2
    exit 1
fi

ensure_target_directory

for source_path in "${skills_dir}"/*; do
    [[ -d "${source_path}" && -f "${source_path}/SKILL.md" ]] || continue
    if [[ "${uninstall}" == true ]]; then
        uninstall_skill "${source_path}"
    else
        install_skill "${source_path}"
    fi
done

echo "Start a new Codex session to discover skill changes."

if [[ "${had_conflict}" == true ]]; then
    exit 1
fi
