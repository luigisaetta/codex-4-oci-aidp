# Split the MCP service into cohesive modules

## Problem and scope

`aidp_mcp/service.py` has 2,161 lines and holds several separate concerns:

* MCP settings loading;
* local-file validation;
* workspace path validation;
* target discovery and caching;
* SDK client construction;
* notebook, job, cluster, catalog, and volume operations;
* response shaping.

It needs a module-level `pylint: disable=too-many-lines`. Its test file,
`aidp_mcp/tests/test_service.py`, has 1,673 lines and 101 tests.

The next planned feature adds AI DP agent tools: code upload, create or update,
deploy, invoke, and traces. Adding them to the same module would push it
towards 3,000 lines, make review harder, and mix new safety-critical code with
unrelated code. The plan-by-default and explicit-confirmation pattern also
exists only as repeated inline code, so new tools could implement it
inconsistently.

Scope:

1. Split `aidp_mcp/service.py` into cohesive modules with a documented
   dependency direction, without behavior changes.
2. Extract the plan-and-confirm pattern into a small shared helper, used by the
   existing mutating operations, without changing their messages or outputs.
3. Split the tests to mirror the new modules while keeping every existing test
   case, and add a snapshot test of the MCP tool contract.
4. In a separate, final step, fix two low-severity findings left over from the
   specification 007 review (see "Follow-up fixes").

Non-goals:

* New tools, new parameters, or AI DP agent support (planned separately).
* Changes to tool names, parameters, defaults, docstrings exposed to MCP
  clients, output fields, error messages, safety gates, polling bounds, or
  target-cache semantics, except the two explicit follow-up fixes.
* Changes to `aidp_common`, `cluster_lifecycle`, or `catalog_tree`.
* Converting operations to asynchronous code or changing SDK client lifetimes.

## Assumptions and prerequisites

* Specifications 002, 004, 005, 007, and 008 describe the current behavior.
  Their tests are the regression baseline.
* Baseline before the change: `pytest --collect-only -q aidp_mcp` reports 101
  tests; the full suite passes (verified 2026-09-27).
* The package is installed in editable mode (specification 008), so the
  `aidp-mcp` command picks up the new modules without reinstallation.

## Interfaces and behavior

### Target module layout

All modules are in the `aidp_mcp` package. Module names are a proposal. Keep
the responsibilities and dependency rules if names are adjusted.

| Module | Responsibility | Current contents (examples) |
| --- | --- | --- |
| `config.py` | MCP settings loading | `McpArgumentParser`, `load_connection_settings` |
| `local_files.py` | Local upload-root policy and local file checks | `PROJECT_ROOT`, `allowed_local_roots`, `validate_local_path`, `validate_local_notebook` |
| `validation.py` | Pure remote-path, name, and limit validation | `validate_workspace_path`, `workspace_content_path`, `validate_workspace_directory`, `validate_workspace_notebook_path`, `validate_resource_name`, `validate_volume_path`, `_normalized_task_notebook_path`, `encoded_content_path`, `_validate_result_limit` |
| `targets.py` | Target discovery, the process-lifetime cache, and SDK client context managers | `ResolvedTarget`, cache and lock, `clear_target_cache`, `_target_cache_key`, `_discover_instances`, `_resolve_target`, workspace and catalog client context managers (today `_clients` and `_catalog_clients`) |
| `lookups.py` | Exact-name resource lookups shared by several domains | `_resource_key`, `find_workspace`, `find_cluster`, `find_cluster_status`, `find_cluster_details`, `JobTarget`, `_sorted_named_resources` |
| `safety.py` | Plan-and-confirm helper (see below) | new |
| `notebooks.py` | Notebook upload and listing | `upload_notebook`, `list_notebooks`, content and object requests, folder creation, missing-content and existing-folder checks, `_notebook_summary` |
| `jobs.py` | Workflow jobs and runs | `find_notebook_jobs`, `list_job_runs`, `ensure_notebook_job`, `start_notebook_job`, run polling, `get_job_run`, `get_job_run_output`, `_find_job`, `_managed_task`, `_is_supported_job`, `_matching_notebook_tasks`, run and output response helpers, `TERMINAL_JOB_STATES` |
| `clusters.py` | Cluster status and lifecycle | `get_cluster_status`, `set_cluster_state`, lifecycle polling, `_cluster_transition_states`, `_submit_cluster_action`, cluster, shape, and worker response helpers |
| `volumes.py` | Catalog volumes and volume-file trees | `list_catalog_volumes`, `list_volume_files`, exact catalog/schema/volume lookups, volume summaries, tree construction and traversal |
| `service.py` | Facade used by `server.py` | `AidpWorkflowService` only |

Keep bounds constants (`MAX_*`) in the module that uses them. A constant used
by several modules goes in `validation.py`.

### Dependency rules

* **Direction:** `server.py` → `service.py` → domain modules (`notebooks`,
  `jobs`, `clusters`, `volumes`) → shared modules (`targets`, `lookups`,
  `validation`, `local_files`, `safety`, `config`) → `aidp_common`.
* **Domain modules must not import each other.** Put anything two domains need
  in a shared module. This matches the existing repository rule that feature
  modules do not import each other.
* **No reverse dependencies:** shared modules never import domain modules,
  `service.py`, or `server.py`.
* **No circular imports.** Add a small offline test that imports every module
  in a fresh interpreter, for example with `importlib` in a subprocess, so a
  cycle fails the suite.

### Facade

* `aidp_mcp.service.AidpWorkflowService` keeps its constructor
  (`settings=None`, loading settings lazily as today) and every public method
  with its current signature, defaults, docstring, return value, and raised
  exceptions.
* Each public method delegates to one domain-module function that receives the
  validated settings explicitly, for example
  `notebooks.upload_notebook(self.settings, local_path, ...)`.
* `server.py` keeps importing only `AidpWorkflowService` and needs no change
  beyond, at most, the module header date.
* **Do not re-export internal helpers from `service.py`.** Tests that patch
  `service.<helper>` must fail loudly (`AttributeError` from `monkeypatch`)
  until they patch the module that actually uses the helper. This prevents
  stale patches that silently stop taking effect.

### Plan-and-confirm helper (`safety.py`)

Provide a small helper that makes the pattern explicit and reusable by the
future agent tools. For example:

* `require_confirmation(confirmed, message)` raises `AidpError(message)` when
  `confirmed` is not exactly `True`. Callers pass their current message
  verbatim, for example "Set confirm_start=true to submit a compute-consuming
  job run."
* `should_apply(apply, action)` returns whether a planned mutation must be
  submitted. It returns false for `apply=false` or when the planned action is a
  no-op such as `unchanged`.

Rules:

* Use the helpers in the existing mutating operations: `upload_notebook`,
  `ensure_notebook_job`, `start_notebook_job`, and `set_cluster_state`.
  Messages, check order, and outputs stay byte-identical.
* Checks still run before any remote client is created, where they do today.
  A missing confirmation must not trigger OCI discovery or any SDK call.
* Document the convention in the module docstring: read-only by default,
  explicit boolean flags for mutations, validation before remote calls, and no
  remote writes in plan mode.
* Keep the helper minimal. Do not add decorators, registries, or generic
  result classes.

### Tests

* Split `aidp_mcp/tests/test_service.py` into files that mirror the modules,
  for example `test_config.py`, `test_local_files.py`, `test_targets.py`,
  `test_notebooks.py`, `test_jobs.py`, `test_clusters.py`, `test_volumes.py`,
  `test_safety.py`, `test_server.py`. Move shared fixtures to
  `aidp_mcp/tests/conftest.py`.
* Keep every existing test case. Moving and renaming are allowed. Weakening
  assertions is not.
* Update `monkeypatch` targets to the module where each name is looked up.
  The target-cache isolation fixture must clear the cache in `targets`.
* Add `test_safety.py` covering both helpers, including non-boolean truthy
  values such as `"true"` and `1`, which must be rejected as today.
* **MCP contract snapshot.** Before moving any code:
  * generate a JSON fixture with the list of MCP tools, each with its name,
    description, and input schema, from `aidp_mcp.server.MCP` using an
    in-memory FastMCP client;
  * commit it as `aidp_mcp/tests/fixtures/mcp_tools.json`;
  * add a test that compares the live tool list with the fixture.

  The fixture must stay unchanged by this specification. Any future
  intentional contract change updates it explicitly and visibly.

### Follow-up fixes (separate final commit)

After the pure refactor passes all checks, apply these two changes in a
separate commit so reviewers can distinguish them:

1. **Single validation pass in `upload_notebook`.** Today the local path is
   validated twice: once by `validate_local_notebook` and again by
   `validate_local_path` to obtain the matching root. Make
   `validate_local_notebook` return the matching root together with its
   current values, and remove the second call. Outputs are unchanged.
2. **Clearer message for relative paths with a non-repository root.** With
   exactly one configured root that is not `PROJECT_ROOT`, a relative path is
   resolved against the server repository and rejected with "must be inside an
   allowed root". Accept relative paths only when the sole allowed root is
   `PROJECT_ROOT`. In every other configuration, reject them with the existing
   "must be absolute" wording, generalized so it no longer says "multiple".
   Update the `upload_notebook` docstring in `server.py`, the README, and the
   MCP tool snapshot fixture if the docstring change alters the tool
   description. This is the only allowed fixture change, and it must be in
   this follow-up commit.

### Documentation

* `aidp_mcp/README.md`: add a short "Code layout" section listing the modules,
  their responsibilities, and the dependency rules, and state where new
  domains such as agents are added.
* Remove the `too-many-lines` Pylint disable from `aidp_mcp`. No new
  module-level Pylint disables may be introduced.
* `CHANGELOG.md`: one `Unreleased` entry for the refactor (internal, no
  user-visible change) and one for the follow-up fixes.
* Specification 007: add a note that its two residual findings are resolved
  by this specification.

## Execution plan (authoritative)

This plan supersedes any earlier step list for this specification. The
follow-up fixes and the MCP snapshot fixture are already committed
(`8b961c7`), and step 0 (restoring all code to `service.py`) is committed
(`a1cd4e8`). Execute the remaining work in this order, one commit per step.

Shared modules first, so that domain modules never need to copy helpers:

1. `validation.py` and `lookups.py`, with the contents listed in "Target
   module layout".
2. `config.py`, `local_files.py`, and `targets.py`, with the contents listed
   in "Target module layout".

Then the domain modules, which import helpers only from the shared modules:

3. `volumes.py`
4. `clusters.py`
5. `jobs.py`
6. `notebooks.py`

Final step:

7. Cleanup:
   * `service.py` contains only the `AidpWorkflowService` facade;
   * re-add the fresh-interpreter import test;
   * remove `max-module-lines` and `min-similarity-lines` from
     `pyproject.toml` and fix any duplication or length findings instead of
     suppressing them;
   * update `aidp_mcp/README.md` "Code layout" and `CHANGELOG.md`.

Rules that apply to every step:

* **Real code only.** Move the actual implementation into each module. A
  module must never only re-import or delegate to `service.py`, and nothing
  except `server.py` and tests may import `service.py`.
* **One definition per helper.** Each function exists in exactly one module.
  Temporary copies are not allowed.
* **Tests move with the code.** Move each moved function's tests into the
  mirrored test file (for example `test_validation.py`). Patch the module
  where each name is used; do not use import aliases.
* **Green at every commit:**
  * the full pytest suite passes;
  * the MCP snapshot fixture is unchanged;
  * Black is clean;
  * Pylint scores 10.00/10 with no new disables, including inline ones. For
    example, rename a local variable that shadows a module, and make trailing
    parameters keyword-only instead of disabling
    `too-many-positional-arguments`.
* **Evidence per step.** After each commit, add one short entry to
  "Verification evidence": the step, the commit, the line counts of
  `service.py` and of the new modules, and the test counts.
* **Finish the step, then commit.** Unfinished work within a step, such as
  tests not yet moved or patch targets not yet updated, is not a reason to
  stop. Complete it, then commit. Never discard or reset working changes, and
  never reset this specification file.
* **Stop only on a real blocker.** A blocker is a problem that cannot be
  solved within these rules, for example an unavoidable import cycle. In that
  case, stop without adding shims or suppressions, keep the uncommitted work
  in the tree, and describe the blocker in "Verification evidence".

## API design and permissions

No OCI or AI DP operation, permission, request, or payload changes. The
refactor is local to the `aidp_mcp` package.

## Acceptance and verification

Offline, with network blocked as today:

* The MCP tool snapshot test passes. The fixture is byte-identical between
  its creation and the end of the refactor commit.
* `pytest --collect-only -q aidp_mcp` reports at least 101 tests before the
  follow-up commit. The evidence section lists any renamed or moved test and
  where it now lives.
* The full pytest suite passes after the refactor commit and again after the
  follow-up commit.
* The fresh-interpreter import test passes. `service.py` defines only
  `AidpWorkflowService` and imports.
* No domain module imports another domain module. Verify with a small test or
  a `grep` recorded in the evidence section.
* Each module is below about 600 lines. If one exceeds that, record why.
* Follow-up tests:
  * `upload_notebook` validates the local path once, checked with a spy or
    patch;
  * a relative path with a single non-repository root is rejected with the
    "must be absolute" message;
  * a relative path with the default root is still accepted.

Manual verification, recorded in the evidence section:

* The installed `aidp-mcp` executable, started from a directory outside the
  repository by a non-interactive FastMCP client, lists all 12 tools.

Quality gates:

* Black and Pylint 10.00/10 on the paths in `DEVELOPMENT.md`, with no
  module-level disables in `aidp_mcp`;
* the full pytest suite in the `codex-4-oci-aidp` Conda environment;
* `git diff --check` clean.

Remote verification, explicit and opt-in: in a new Codex session, call the
read-only tools `get_cluster_status` and `list_notebooks`, and plan (with
`apply=false`) one notebook upload. Record sanitized results only.

## Verification evidence

First attempt (commits `7b4312e` and `8b961c7`): incomplete. It introduced
domain and shared-module delegation shims while leaving all real logic in
`aidp_mcp/operations.py` (2,162 lines), and used Pylint configuration changes
to accommodate that monolith. Its passing tests and manual 12-tool check did
not satisfy this specification's extraction acceptance criteria.

Restart, Step 0 (2026-09-27): `operations.py` and every delegation shim were
removed. The actual implementation was restored to `service.py`, which is the
only parent module pending incremental extraction. `test_service.py` now
imports `aidp_mcp.service` directly, without an alias. In the
`codex-4-oci-aidp` Conda environment, the MCP suite passed (113 tests), the
full offline suite passed (196 tests), Pylint scored 10.00/10, and `git diff
--check` was clean. The MCP snapshot is unchanged in this step.

Step 1 — validation and lookups (commit `Extract MCP validation and lookup
helpers`, 2026-09-27): moved the real
remote-path, resource-name, encoding, result-limit, and exact-resource lookup
helpers to `validation.py` (161 lines) and `lookups.py` (161 lines). Direct
validation tests moved to `test_validation.py` and use that module directly.
`service.py` is 1,882 lines. The MCP suite collected 113 tests; the full suite
passed 196 tests, Black was clean, Pylint scored 10.00/10, `git diff --check`
was clean, and the MCP snapshot was unchanged.

Micro-step 2a — local files (commit `Extract MCP local file validation`,
2026-09-27): moved `PROJECT_ROOT`, allowed-root selection, local-path
validation, and notebook parsing to `local_files.py` (108 lines). Their direct
tests moved to `test_local_files.py`; the upload validation spy now patches
`local_files.validate_local_path`. `service.py` is 1,791 lines. The MCP suite
collected 113 tests; the full suite passed 196 tests, Black was clean, Pylint
scored 10.00/10, `git diff --check` was clean, and the MCP snapshot was
unchanged.

Micro-step 2b — configuration (commit `Extract MCP configuration loading`,
2026-09-27): moved `McpArgumentParser` and connection-settings loading to
`config.py` (63 lines), which imports allowed-root selection from
`local_files.py`. The related tests moved to `test_config.py` and patch
`config.connection_parser`. `service.py` is 1,738 lines. The MCP suite
collected 113 tests; the full suite passed 196 tests, Black was clean, Pylint
scored 10.00/10, `git diff --check` was clean, and the MCP snapshot was
unchanged.

Micro-step 2c — targets (commit `Extract MCP target discovery`, 2026-09-27):
moved target identifiers, the process cache, target discovery, and target
resolution to `targets.py` (163 lines); client context managers remain in
`service.py`. Cache, discovery, and resolution tests moved to
`test_targets.py`, whose autouse fixture calls `targets.clear_target_cache`.
Patches now target `targets._discover_instances`, `targets.list_instances`,
and `targets.find_workspace` where those names are resolved. `service.py` is
1,595 lines. The MCP suite collected 113 tests; the full suite passed 196
tests, Black was clean, Pylint scored 10.00/10, `git diff --check` was clean,
and the MCP snapshot was unchanged.

Micro-step 2d — target client contexts (commit `Extract MCP target clients`,
2026-09-27): moved the workspace and catalog SDK client context managers to
`targets.workspace_clients(settings)` and `targets.catalog_clients(settings)`.
`service.py` calls those functions; test client injection and
`managed_client`/`ExitStack` patches now target `targets`. `service.py` is
1,486 lines and `targets.py` is 274 lines. The MCP suite collected 113 tests;
the full suite passed 196 tests, Black was clean, Pylint scored 10.00/10,
`git diff --check` was clean, and the MCP snapshot was unchanged.

Step 3 — volumes (commit `Extract MCP volume operations`, 2026-09-27): moved
the real catalog/volume discovery, exact catalog/schema/volume resolution,
summaries, mount-path normalization, bounded tree construction, and shallow
recursive-tree traversal to `volumes.py` (409 lines). `service.py` is 1,113
lines and delegates the two public operations to that domain module; all six
related tests moved to `test_volumes.py`, where client and pagination patches
target `volumes`. The MCP suite collected 113 tests; the full suite passed 196
tests, Black was clean, Pylint scored 10.00/10, `git diff --check` was clean,
and the MCP snapshot was unchanged. Manual local MCP verification confirmed
that the expected 12 tools remain registered.

Remote verification: pending. In an authorized new Codex session, call
`get_cluster_status` and `list_notebooks`, then plan one notebook upload with
`apply=false`; record only sanitized results.
