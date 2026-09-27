# MCP server robustness and configurable local upload roots

## Problem and scope

Three defects in `aidp_mcp` limit its reliability and prevent its use from
repositories other than this one. They must be fixed before the server is
extended with AI DP agent tools and used together with Codex skills.

1. **Configuration errors terminate the server.** `load_connection_settings()`
   (`aidp_mcp/service.py`) reports invalid or incomplete settings through
   `argparse.ArgumentParser.error()`, which raises `SystemExit`. The same
   happens inside `aidp_common.settings.connection_parser()` and
   `validate_connection()`. `SystemExit` is a `BaseException`, not an
   `Exception`, so FastMCP does not convert it into a tool error.
   Verified locally on 2026-09-27 with `fastmcp` 3.4.5 and an in-memory
   `fastmcp.Client`: a tool that calls `parser.error()` ends the session
   (`CancelledError`), and the next call to a healthy tool fails with
   `ClosedResourceError`. A missing `WORKSPACE_NAME` therefore kills the MCP
   server instead of returning an actionable message.
2. **The launcher depends on the client's working directory.**
   `scripts/start_aidp_mcp.sh` runs `python -m aidp_mcp.server` without
   changing directory. The module is importable only when the MCP client
   starts the process from the repository root. When Codex runs in another
   project, startup fails with `ModuleNotFoundError`.
3. **Uploads are restricted to this repository.** `validate_local_notebook()`
   accepts only files inside `PROJECT_ROOT`, the repository that contains the
   server. The restriction is a valid security control: it prevents a model
   from uploading arbitrary local files such as SSH or OCI keys. However, it
   makes it impossible to upload notebooks developed in another project.

Scope:

1. Convert configuration errors into `AidpError` so tools return an error and
   the server keeps running.
2. Make the launcher independent of the caller's working directory.
3. Replace the single repository root with an explicit, operator-configured
   list of allowed local roots, defaulting to the current behavior.
4. Add `aidp_mcp` to the Black and Pylint commands in `DEVELOPMENT.md`, which
   currently omit it.

Non-goals:

* Packaging the project as an installable distribution or adding a console
  entry point (planned separately).
* Splitting `aidp_mcp/service.py` into modules (planned separately).
* Directory or multi-file uploads, secret-file exclusion lists, and AI DP
  agent tools. The allowed-roots helper is designed to be reused by them later.
* Changing tool names, parameters, safety gates, or output fields other than
  those listed below.
* Changing the command-line behavior of `cluster_lifecycle` and
  `catalog_tree`, which are one-shot processes where `SystemExit` with a
  non-zero code is correct.

## Assumptions and prerequisites

* The MCP server runs as a long-lived stdio child process started by Codex,
  as documented in `aidp_mcp/README.md`.
* Settings are still read from the root `.env` on every request, with process
  environment variables taking precedence (unchanged).
* The allowed-roots list is operator configuration. It must never be a tool
  parameter, so a model cannot widen it.

## Interfaces and behavior

### 1. Configuration errors as tool errors

* `load_connection_settings()` must raise `AidpError`, never `SystemExit`,
  for any invalid or incomplete setting. This includes errors raised while
  building the parser (for example, an unreadable or missing explicit `.env`)
  and errors from `validate_connection()`.
* The `AidpError` message must preserve the actionable argparse message (for
  example, "Set WORKSPACE_NAME before using AI DP MCP tools."). One
  acceptable approach is an `argparse.ArgumentParser` subclass, used only by
  the MCP path, whose `error()` raises instead of exiting. Converting
  `SystemExit` after the fact is not sufficient because argparse writes the
  message to standard error and `SystemExit` carries only the exit code.
* Messages must not include setting values, file contents, or credentials.
  Naming a variable (for example, `WORKSPACE_NAME`) is allowed.
* Update the `load_connection_settings()` docstring, which currently
  documents `SystemExit`.
* Keep the existing `SystemExit` behavior for the `cluster_lifecycle` and
  `catalog_tree` command-line tools. If `aidp_common` needs a new
  parameter or helper to support both behaviors, keep it small and covered by
  tests.

### 2. Working-directory-independent launcher

* `scripts/start_aidp_mcp.sh` must change to the repository root, derived from
  the script location, before starting the server. For example,
  `cd -- "$(dirname -- "$0")/.."` with `CDPATH` unset, or the same approach
  used in `scripts/start_oci_api_mcp.sh`.
* Keep `set -euo pipefail`, `exec`, and the `conda run --no-capture-output`
  invocation. Update the header comment to state that the script can be
  launched from any working directory.
* No output may be written to standard output before `exec`, because standard
  output carries MCP messages.

### 3. Configurable allowed local roots

New optional setting, documented in `.env.example`:

```bash
# MCP uploads only: local directories from which files may be uploaded,
# separated by ':' (os.pathsep). Empty means this repository only.
AIDP_ALLOWED_ROOTS=
```

Behavior:

* Read `AIDP_ALLOWED_ROOTS` with the same precedence as other settings:
  process environment, then `.env`. When it is empty or absent, the only
  allowed root is `PROJECT_ROOT`, which is the current behavior.
* Split on `os.pathsep`, expand `~`, and resolve each entry with
  `Path.resolve()`. Ignore empty entries.
* When two or more allowed roots are configured, require an absolute
  `local_path`. A relative path would otherwise resolve from the server's
  repository root after launcher startup and could silently select a notebook
  from a different allowed project. Raise an `AidpError` that asks for an
  absolute path without listing configured roots. A `~/...` input remains
  valid because expansion makes it absolute before this check.
* Reject the whole configuration with an `AidpError` if an entry:
  * does not exist or is not a directory; or
  * is the filesystem root, the user's home directory, or an ancestor of the
    home directory. These roots are too broad and would expose keys and
    configuration files.
* Replace the containment check in `validate_local_notebook()` with a helper,
  for example `validate_local_path(local_path, roots)`, that:
  * resolves the candidate with `Path.resolve()` before checking, so symbolic
    links cannot escape an allowed root; and
  * accepts the candidate only if it is inside at least one allowed root.
* Keep all other notebook checks unchanged: `.ipynb` suffix, existing regular
  file, valid UTF-8 JSON, nonempty object, and SHA-256 digest.
* The error for a path outside every root must name `AIDP_ALLOWED_ROOTS`, for
  example: "Local notebook path must be inside an allowed root; see
  AIDP_ALLOWED_ROOTS." It must not list the configured roots.

Output change in `upload_notebook`:

* `local_path` is currently computed with
  `local_file.relative_to(PROJECT_ROOT)`, which raises `ValueError` for files
  outside the repository. Report `local_path` relative to the matching
  allowed root, and add `local_root` containing only that root's final
  directory name (for example, `my-langgraph-agent`). Do not return absolute
  local paths, which expose the local user name.
* For files inside this repository with the default configuration,
  `local_path` must stay identical to the current output.

### 4. Development checks

* Add `aidp_mcp` to the `black` and `pylint` commands in `DEVELOPMENT.md`,
  including `aidp_mcp/tests/*.py` for Pylint, consistent with the other
  features.

### Documentation

* `aidp_mcp/README.md`: document `AIDP_ALLOWED_ROOTS`, its default, its
  rejected values, and why it is not a tool parameter. State that the
  launcher can be registered once and used from any Codex project.
* `.env.example`: add the setting with the comment shown above.
* `CHANGELOG.md`: one `Unreleased` entry dated on the implementation day,
  including the new `local_root` output field.

## API design and permissions

No OCI or AI DP operation, permission, or request changes. The change is
local: settings validation, process startup, and local filesystem checks.

## Acceptance and verification

Offline tests in `aidp_mcp/tests/test_service.py` (and in
`aidp_common/tests/` if `aidp_common` changes), with network blocked as today:

Configuration errors:

* With `WORKSPACE_NAME` unset, `load_connection_settings()` raises
  `AidpError` whose message names `WORKSPACE_NAME`, and does not raise
  `SystemExit`.
* An invalid `REGION` raises `AidpError`, not `SystemExit`.
* Server survival (regression test for the verified defect): using an
  in-memory `fastmcp.Client` on `aidp_mcp.server.MCP`, call a tool with
  incomplete settings. The call returns an error result (`is_error` true) with
  the actionable message. A second call on the same client session is then
  processed normally, for example with valid patched settings and a mocked
  service, and does not raise `ClosedResourceError`.
* The `cluster_lifecycle` and `catalog_tree` test suites still pass
  unchanged, confirming their command-line `SystemExit` behavior.

Allowed roots:

* Default: with `AIDP_ALLOWED_ROOTS` unset, a notebook inside the patched
  `PROJECT_ROOT` is accepted and a notebook outside it is rejected. Existing
  tests keep passing.
* Two configured roots: a notebook in the second root is accepted;
  `local_path` is relative to that root and `local_root` is its directory
  name.
* When the same relative notebook path exists in the server repository and a
  second configured root, the relative input is rejected with an actionable
  absolute-path error rather than selecting either file.
* A symbolic link inside an allowed root that points to a file outside every
  root is rejected.
* Configurations containing `/`, the home directory, an ancestor of the home
  directory, a missing path, or a regular file are rejected with
  `AidpError`. Use `tmp_path` and patch the home-directory lookup; tests must
  not depend on the real home directory.
* The rejection message names `AIDP_ALLOWED_ROOTS` and does not contain the
  configured paths.
* Precedence: a process environment value overrides the `.env` value.
* `upload_notebook` with `apply=false` for a notebook in an extra root returns
  the plan without calling any remote write operation (mocked clients).

Launcher:

* Manual check, recorded in the evidence section: from a directory outside
  the repository, run `codex mcp list` in a new Codex session, or start the
  launcher with a scripted MCP `initialize` request. The server starts and
  lists its tools. Do not start it in an interactive terminal without a
  client.

Quality gates: Black and Pylint 10.00/10 on `aidp_common`, `aidp_mcp` (including
tests), `cluster_lifecycle`, `catalog_tree`, and `conftest.py`; the full pytest
suite in the `codex-4-oci-aidp` Conda environment; `git diff --check` clean.

Remote verification, explicit and opt-in: with `AIDP_ALLOWED_ROOTS` pointing
to a second local project, ask Codex (started in that project) to plan the
upload of one of its notebooks with `apply=false`. Confirm that the plan is
returned and nothing is written. Record sanitized results only: no OCIDs,
absolute paths, or user names.

## Verification evidence

Implemented and locally verified on 2026-09-27.

Local environment:

* Conda environment: `codex-4-oci-aidp`.
* Python: 3.11.0.
* Verification used only offline unit tests; the shared test fixture blocked
  live HTTP requests.

Local results:

* `python -m black aidp_common aidp_mcp cluster_lifecycle catalog_tree
  conftest.py` completed with no further changes; the matching `--check`
  command passed.
* `python -m pylint aidp_common aidp_common/tests/*.py aidp_mcp
  aidp_mcp/tests/*.py cluster_lifecycle/*.py cluster_lifecycle/tests/*.py
  catalog_tree/*.py catalog_tree/tests/*.py conftest.py` scored 10.00/10.
* `python -m pytest -q` passed: 172 tests.
* `git diff --check` passed.
* Offline tests confirm that missing workspace configuration, invalid region,
  and a missing explicitly selected dotenv file raise `AidpError` rather than
  `SystemExit`. An in-memory FastMCP client receives a configuration error and
  successfully completes a subsequent call in the same session.
* Offline allowed-root tests cover the repository default, a second configured
  root, symlink escape rejection, filesystem/home/ancestor/missing/file root
  rejection without path disclosure, process-environment precedence, and an
  `apply=false` upload plan that issues no remote write request. The plan
  reports a root-relative `local_path` and a directory-name-only `local_root`.
  They also confirm that duplicate relative paths in two configured roots are
  rejected before the launcher working directory can select the server copy.
* A non-interactive FastMCP client started the launcher with its working
  directory outside the repository and listed all 12 registered tools. No
  interactive MCP server was started.

Remote verification: pending. With explicit authorization and a configured
second local project root, start Codex in that project and request one
`apply=false` notebook upload plan. Confirm that no remote write occurs and
record only sanitized output; do not include OCIDs, absolute paths, or local
user names.
