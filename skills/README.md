# AI DP operational skills

This repository has two classes of Codex skills:

* Repository-development skills live in `.agents/skills/`. They guide Codex
  only while working in this repository and are discovered at repository scope.
* Operational AI DP skills live in this `skills/` directory. They are versioned
with the AI DP MCP server and can be made available to Codex in any project.

## Available operational skills

| Skill | Use it for | Safety boundary |
| --- | --- | --- |
| [`aidp-notebook-deploy-and-run`](aidp-notebook-deploy-and-run/SKILL.md) | Uploading an approved notebook, reconciling its managed job, and running it through `aidp-mcp` | Every mutation and job start requires current-conversation approval. |
| [`aidp-agent-deploy`](aidp-agent-deploy/SKILL.md) | Deploying, redeploying, and smoke-testing an existing code-first LangGraph agent through `aidp-mcp` | It does not author agent code or fall back to scripts, SDK, CLI, or REST; every mutation, compute start, and invocation requires current-conversation approval. |

The agent deployment skill requires an already available `aidp-mcp` server and
an absolute local path for the upload step. It plans each mutation first,
requires deployment after every code upload, and stops after one failed deploy
or smoke test instead of retrying automatically.

## Install and update

The verified user-scope skill location is `$HOME/.agents/skills`. The empirical
check recorded in [Spec 010](../specs/010-codex-skills-infrastructure.md)
confirmed this location on 2026-09-27 with `codex-cli 0.155.0-alpha.16.3`.

From any directory, install the operational skills with:

```bash
/path/to/codex-4-oci-aidp/scripts/install_skills.sh
```

The script creates symbolic links from `$HOME/.agents/skills` to the skill
folders in this directory. It never replaces a regular file, directory, or a
link owned by another source. Use `--dry-run` to preview actions, or `--target`
to check an alternate user-scope directory safely:

```bash
scripts/install_skills.sh --dry-run
scripts/install_skills.sh --target /tmp/aidp-skills-check
```

Because installation uses symbolic links, changes to a source skill are
available immediately to future sessions; run the installer again only when a
skill folder is added or removed. Start a **new Codex session** after any
installation, update, or removal so Codex rescans the skills.

## Uninstall

Remove only links that point into this repository with:

```bash
scripts/install_skills.sh --uninstall
```

The command reports a conflict and leaves it unchanged if a same-named target
is a regular file, directory, or a link that points elsewhere.
