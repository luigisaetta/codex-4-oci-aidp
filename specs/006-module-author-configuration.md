# Configurable Python module attribution

## Problem and scope

The required Python module header named one individual in the versioned
repository instructions. That makes the project less reusable and would apply
the same attribution to code created by every contributor or agent.

This change makes the author field locally configurable while preserving the
current local default for this checkout.

Scope:

1. Define a `CODE_AUTHOR` local configuration value for Python module headers.
2. Set this checkout's author attribution in its ignored root `.env`.
3. Keep the versioned instructions and configuration template free of a
   personal name, with `Project contributor` as the fallback.

Non-goals:

* Retroactively changing existing Python headers.
* Reading attribution at Python runtime or exposing it through application
  configuration.
* Adding a new dependency or an automated source-code rewrite.

## Assumptions and prerequisites

Codex agents working in this repository can read the root `.env` when creating
or editing Python files. The file is excluded from Git, so it may contain the
local attribution preference without propagating it to clones.

## Intended behavior

When creating or updating a Python module header, an agent uses the root
`.env` value `CODE_AUTHOR` if it is present and non-empty. If the value is
missing, the header contains `Author: Project contributor`. The header keeps
the required date, license, and description fields.

This checkout's local `.env` supplies `CODE_AUTHOR`; consequently, new or
modified Python modules use its configured attribution.

## Acceptance criteria and verification

* `AGENTS.md` documents `CODE_AUTHOR`, the generic fallback, and the header
  template without a personal name.
* The ignored local `.env` supplies a non-empty `CODE_AUTHOR` value.
* `.env.example` does not contain `CODE_AUTHOR` or a personal author name.
* `CHANGELOG.md` records the change under `Unreleased` with the current date.

Verification is a documentation and configuration review: inspect the three
files above and confirm that the local `.env` remains ignored by Git. No Python
runtime behavior or OCI resource is affected.
