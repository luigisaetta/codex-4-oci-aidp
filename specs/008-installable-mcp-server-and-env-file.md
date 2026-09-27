# Installable MCP server and explicit settings file

## Problem and scope

The AI DP MCP server is meant to be used from Codex sessions opened in other
projects, for example a LangGraph agent repository. Two gaps remain after
specification 007:

1. **The server is not an installable command.** `pyproject.toml` contains only
   tool configuration and no `[project]` table. The only way to start the
   server is `scripts/start_aidp_mcp.sh`, which depends on this checkout's
   layout and on `conda run`. There is no stable executable to register with
   an MCP client, and no packaging metadata recording the runtime dependencies.
2. **The settings file cannot be selected explicitly.** The MCP server always
   reads `<repository>/.env` (`aidp_common.settings.DEFAULT_ENV_FILE`). This
   works only because the default is derived from the source location. An
   operator cannot point the server to a different settings file, for example
   one per AI DP environment, without editing this repository's `.env`.
   `scripts/start_oci_api_mcp.sh` already honors an `AIDP_ENV_FILE` variable,
   but the Python features do not, which is inconsistent.

Scope:

1. Add a `[project]` table with runtime dependencies and an `aidp-mcp` console
   entry point, and support an editable installation (`pip install -e .`) in
   the project Conda environment.
2. Add an `AIDP_ENV_FILE` process environment variable, handled in
   `aidp_common`, that selects the settings file for the MCP server and for the
   existing command-line features.
3. Document how to register the installed command with Codex, including how
   to pass `AIDP_ENV_FILE`.

Non-goals:

* Publishing to PyPI or any package index, building release artifacts, or
  supporting non-editable (wheel or regular) installations. See "Supported
  installation mode".
* Console entry points for `cluster_lifecycle` and `catalog_tree`. They are
  folder-level scripts with top-level modules and remain run as today.
* Splitting `aidp_mcp/service.py` (planned separately).
* Removing `scripts/start_aidp_mcp.sh`. It remains a supported launcher.
* Changing tool names, parameters, outputs, or safety gates.

## Assumptions and prerequisites

* The `codex-4-oci-aidp` Conda environment exists with Python 3.11 and the
  current pins, and the Oracle SDK wheel has been downloaded to `.deps/` as
  documented in the root `README.md`.
* `aidp-python-client` is distributed by Oracle as a wheel inside a release ZIP,
  not on PyPI. Its distribution name is `aidp-python-client` and its version is
  4.2.1 (verified from the wheel metadata on 2026-09-27).
* setuptools 83.0.0 and pip 26.2.1 are present in the environment (verified
  2026-09-27). Both support PEP 621 metadata and PEP 660 editable installs.
* `AIDP_ENV_FILE` is operator configuration. It must never be a tool
  parameter, so a model cannot select a different settings file.

## Interfaces and behavior

### 1. Packaging metadata

Add to the root `pyproject.toml`, keeping all existing `[tool.*]` sections:

* `[build-system]`: setuptools 77 or later as the build backend. This minimum
  supports PEP 621, PEP 660, and the PEP 639 SPDX `license = "MIT"` string.
* `[project]`:
  * `name = "codex-4-oci-aidp"`, a version such as `0.1.0`, a one-line
    description, `requires-python = ">=3.11"`, and the MIT license consistent
    with `LICENSE`.
  * `dependencies`: the same direct runtime dependencies and exact pins as
    `requirements.txt`: `aidp-python-client==4.2.1`, `oci==2.165.1`,
    `python-dotenv==1.2.3`, `fastmcp==3.4.5`. Reference `aidp-python-client`
    by name and version, not by a local file path. A PEP 508 direct reference
    would need an absolute `file://` URL, which is machine-specific.
* `[project.scripts]`: `aidp-mcp = "aidp_mcp.server:main"`.
* Package discovery: include only the `aidp_common` and `aidp_mcp` packages.
  Do not package `cluster_lifecycle`, `catalog_tree`, `notebooks`, `specs`,
  `scripts`, or the tests. Use an explicit package list or a discovery rule
  with explicit excludes; do not rely on automatic flat-layout discovery,
  which would fail or pick up unintended directories.

`requirements.txt` stays the installation entry point for dependencies
because it references the local SDK wheel. Pins exist in two places, so add
an offline test that parses both files and fails if the direct dependency pins
differ. Ignore comments, the `-r` include, and the local wheel path, which
maps to `aidp-python-client==4.2.1`.

### 2. Supported installation mode

Only the editable installation is supported:

```bash
conda activate codex-4-oci-aidp
python -m pip install -r requirements-dev.txt
python -m pip install --no-deps --no-build-isolation -e .
```

* Use `--no-deps` because dependencies, including the local SDK wheel, are
  already installed from the requirements files. This also prevents pip from
  trying to fetch `aidp-python-client` from PyPI.
* `--no-build-isolation` is the supported offline editable-install option. It
  uses the already installed setuptools build backend instead of creating an
  isolated environment that may require package downloads.
* In an editable install, `aidp_common` and `aidp_mcp` are imported from this
  checkout, so `PROJECT_ROOT` and `DEFAULT_ENV_FILE`, which are derived from
  `__file__`, keep pointing to the repository. Record this in a code comment
  next to `DEFAULT_ENV_FILE`.
* A non-editable install would place the modules in `site-packages`. The
  default settings file and the default allowed upload root would then point
  there. This mode is not supported. The README must say so, and must state
  that `AIDP_ENV_FILE` and `AIDP_ALLOWED_ROOTS` are required if anyone tries
  it anyway. Do not add code to detect it.

### 3. `AIDP_ENV_FILE`

Settings-file selection, from highest to lowest precedence:

1. The command-line `--env-file` option (command-line features only; the MCP
   server passes no arguments).
2. The `AIDP_ENV_FILE` process environment variable.
3. The repository default, `<repository>/.env`.

Behavior, implemented once in `aidp_common.settings.connection_parser` so that
the MCP server, `cluster_lifecycle`, and `catalog_tree` share it:

* Read `AIDP_ENV_FILE` from `os.environ` only. Values in any dotenv file,
  including a key named `AIDP_ENV_FILE`, are ignored for this purpose. It
  selects the file and cannot be defined inside it.
* After `~` expansion, the value must be an absolute path. Reject a relative
  value: it would be resolved against the process working directory, which
  the launcher changes to the repository root. This is the same class of
  defect fixed in specification 007.
* A file selected through `AIDP_ENV_FILE` is treated as explicitly selected.
  If it does not exist, is not a regular file, or cannot be read, report an
  error instead of silently using an empty configuration. Today a missing
  default `.env` silently yields an empty configuration; keep that for the
  repository default only.
* Error reporting uses the parser's existing mechanism. The MCP parser from
  specification 007 turns it into `AidpError` and the server stays alive; the
  command-line features exit with argparse's usage error, as today. Messages
  name `AIDP_ENV_FILE` or `--env-file` and must not include the path or any
  file content.
* `scripts/start_oci_api_mcp.sh` already reads `AIDP_ENV_FILE`. Align its
  documentation with the rules above (absolute path). Changing its parsing
  logic is out of scope.
* When the repository default `.env` is absent and `AIDP_ENV_FILE` is unset,
  the MCP configuration error for missing required settings must mention both
  the root `.env` and `AIDP_ENV_FILE`. The server can be started from another
  project, and the operator needs to know where settings come from.

Update `.env.example` with a comment stating that `AIDP_ENV_FILE` is set in
the process environment, for example in the MCP client configuration, and not
in this file.

### 4. Codex registration

Document two supported registrations in `aidp_mcp/README.md`.

Preferred, using the installed entry point:

```bash
codex mcp add aidp-mcp \
  --env AIDP_ENV_FILE=/absolute/path/to/aidp.env \
  -- /absolute/path/to/conda/envs/codex-4-oci-aidp/bin/aidp-mcp
```

* Show how to obtain the absolute executable path, for example
  `conda run -n codex-4-oci-aidp python -c "import shutil; print(shutil.which('aidp-mcp'))"`.
  Do not hard-code a user-specific path in documentation.
* Calling the environment's executable directly avoids `conda run` and does
  not depend on the caller's working directory.
* `--env` is optional. Without it, the repository `.env` is used.
* The preferred installed registration replaces an existing `aidp-mcp` entry;
  document `codex mcp remove aidp-mcp` before adding it. The installed and
  launcher registrations must not both be active, because they expose the
  same tool names twice.
* Verify the `codex mcp add` option syntax for passing environment variables
  against the official OpenAI Codex MCP documentation, record the source and
  verification date in this specification, and show the equivalent
  `config.toml` form (`[mcp_servers.aidp-mcp]` with `command` and an `env`
  table). Do not invent options. If the CLI has no option for environment
  variables, document only the `config.toml` form.

Existing launcher: `scripts/start_aidp_mcp.sh` remains valid. It may pass the
caller's `AIDP_ENV_FILE` through unchanged; `conda run` inherits the
environment. Document this, but do not make the launcher set the variable.

Official syntax verification: on 2026-09-27, the [OpenAI Codex MCP
documentation](https://developers.openai.com/codex/mcp/) documented
`codex mcp add <server-name> --env VAR=VALUE -- <stdio-server-command>` and
the `[mcp_servers.<server-name>.env]` TOML table for stdio-server environment
variables. The README examples use that documented form.

## API design and permissions

No OCI or AI DP operation, permission, or request changes. The change affects
local packaging, process startup, and settings-file selection only.

## Acceptance and verification

Offline tests, with network blocked as today:

Settings-file selection (`aidp_common/tests/`):

* `AIDP_ENV_FILE` set to an absolute temporary file: its values are used, and
  the repository default file is not read (point `DEFAULT_ENV_FILE` to a
  temporary file with different values to prove it).
* `--env-file` takes precedence over `AIDP_ENV_FILE` for a command-line parser.
* Process environment variables still take precedence over values in the
  selected file (for example, `REGION`).
* A relative `AIDP_ENV_FILE` is rejected.
* A missing `AIDP_ENV_FILE` target is rejected: `AidpError` for the MCP
  parser, argparse error (`SystemExit` with code 2) for the command-line
  parser. The message does not contain the path.
* A key named `AIDP_ENV_FILE` inside a dotenv file has no effect on selection.
* Without `AIDP_ENV_FILE`, behavior is unchanged: the repository default is
  used, and a missing default still yields an empty configuration.

MCP server (`aidp_mcp/tests/`):

* With `AIDP_ENV_FILE` pointing to a file missing `WORKSPACE_NAME`, an
  in-memory FastMCP client receives an error result and a subsequent call in
  the same session succeeds (same pattern as specification 007).
* With no settings available, the configuration error mentions both the root
  `.env` and `AIDP_ENV_FILE`.

Packaging:

* The pin-consistency test described in "Packaging metadata" passes, and fails
  when a pin is changed in only one file (verify manually once; do not commit
  a failing case).
* `aidp_mcp.server.main` is importable and callable without arguments (do not
  run the stdio loop in tests).

Manual verification, recorded in the evidence section:

* In the Conda environment, `python -m pip install --no-deps -e .` succeeds;
  `pip show codex-4-oci-aidp` lists the editable location; and the
  `aidp-mcp` executable exists in the environment's `bin` directory.
* From a working directory outside the repository, a non-interactive FastMCP
  client (or a scripted MCP `initialize` plus `tools/list`) started with the
  absolute `aidp-mcp` path lists all 12 tools. Repeat with `AIDP_ENV_FILE`
  pointing to a copy of the settings stored outside the repository. Do not
  start the server in an interactive terminal.
* `scripts/start_aidp_mcp.sh` still starts the server and lists all 12 tools.

Quality gates: Black and Pylint 10.00/10 on the paths listed in
`DEVELOPMENT.md`; the full pytest suite in the `codex-4-oci-aidp` Conda
environment; `git diff --check` clean.

Documentation: root `README.md` (installation commands and supported mode),
`aidp_mcp/README.md` (registration, `AIDP_ENV_FILE`, precedence),
`DEVELOPMENT.md` (editable install step), `.env.example`, and `CHANGELOG.md`
(one `Unreleased` entry dated on the implementation day).

Remote verification, explicit and opt-in: register the installed `aidp-mcp`
in Codex with `AIDP_ENV_FILE` set, start Codex in a different project, and
call one read-only tool, for example `get_cluster_status`. Record sanitized
results only: no OCIDs, absolute paths, or local user names.

## Verification evidence

Implemented and locally verified on 2026-09-27.

Local environment:

* Conda environment: `codex-4-oci-aidp` with Python 3.11.0.
* Tests ran offline; the shared fixture blocked live HTTP requests.
* Codex registration syntax was checked against the [official OpenAI Codex
  MCP documentation](https://developers.openai.com/codex/mcp/) on 2026-09-27.

Local results:

* Offline tests cover `AIDP_ENV_FILE` selection, `--env-file` precedence,
  ordinary environment precedence, relative and missing selector rejection,
  selector redaction, ignored dotenv selector values, and the absent default
  dotenv behavior. They also confirm MCP-session survival for an external
  file missing `WORKSPACE_NAME`, no-settings guidance, importable console
  target, and dependency-pin consistency.
* As a manual negative check, changing only the `fastmcp` metadata pin made
  the pin-consistency test fail with the two mismatched versions; the original
  pin was restored before the final quality run.
* `python -m pip install --no-deps --no-build-isolation -e .` succeeded. `pip show
  codex-4-oci-aidp` reported the editable project location, and the
  environment contained the `aidp-mcp` executable. This command was repeated
  without build isolation on 2026-09-27 after raising the setuptools minimum
  to 77 and succeeded using the installed backend.
* A non-interactive FastMCP client started the installed absolute executable
  from outside the repository and listed all 12 tools, both with the default
  settings and with a temporary external copy selected by `AIDP_ENV_FILE`.
  No OCI tool was called.
* A non-interactive FastMCP client also started
  `scripts/start_aidp_mcp.sh` from outside the repository and listed all 12
  tools.
* `python -m black aidp_common aidp_mcp cluster_lifecycle catalog_tree
  conftest.py` made no changes; the matching `--check` command passed.
  Pylint scored 10.00/10, `python -m pytest -q` passed 184 tests, and
  `git diff --check` passed.

Remote verification: pending. With explicit authorization, register the
installed `aidp-mcp` in Codex with `AIDP_ENV_FILE` set, start Codex in a
different project, and call one read-only tool. Record only sanitized output;
do not include OCIDs, absolute paths, or local user names.
