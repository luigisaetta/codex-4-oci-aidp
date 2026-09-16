# Changelog

## Unreleased

* 2026-09-16: Specify the documented `timeCreated` task-run ordering when
  retrieving notebook job output, avoiding AI DP's rejected null `sortBy`
  request value.

* 2026-09-16: Add the read-only `find_notebook_jobs` MCP tool to find
  configured-workspace workflow jobs that reference an exact workspace notebook.

* 2026-09-16: Add the read-only `list_notebooks` MCP tool for bounded,
  non-recursive notebook metadata discovery in an explicit AI DP workspace
  directory.

* 2026-09-16: Add the confirmed `set_cluster_state` MCP tool to start or stop
  an exact AI DP cluster, with ETag-guarded no-retry submission and optional
  bounded waiting before a notebook job is started.

* 2026-09-15: Fix cluster lifecycle parsing after shared workspace-name
  configuration caused a duplicate `--workspace-name` argument.

* 2026-09-15: Add read-only AI DP MCP tools for exact cluster status and
  bounded plain-text output from managed single-notebook job runs.

* 2026-09-15: Create and submit the `test00_job` AI DP notebook workflow job
  for the uploaded `test00` notebook on cluster `clu02`.

* 2026-09-15: Set the required `ALL_SUCCESS` notebook-task run condition and
  validate AI DP workflow job names before job creation.

* 2026-09-15: Preserve numeric AI DP response timestamps in all MCP workflow
  clients so workflow-job discovery does not fail during SDK date parsing.

* 2026-09-15: Treat AI DP's specific existing-directory conflict as an
  idempotent workspace-folder result during notebook upload.

* 2026-09-15: Fix `test00` notebook cell source newlines so the code cell can
  execute correctly in a Jupyter kernel.

* 2026-09-15: Upload the `test00` example notebook to its configured OCI AI
  Data Platform workspace after a successful read-only destination check.

* 2026-09-15: Avoid double-encoding AI DP notebook URL paths by calling the
  documented notebook-content endpoint through the authenticated SDK client.

* 2026-09-15: Create the AI DP workspace folder and empty notebook before
  uploading content, following Oracle's documented notebook API sequence.

* 2026-09-15: Clarify the MCP notebook upload parameter as workspace-relative
  to prevent invalid notebook-content endpoint paths.

* 2026-09-15: Use workspace-relative notebook content paths in the AI DP MCP
  upload operation, matching the installed SDK endpoint contract.

* 2026-09-15: Expose the configured AI DP workspace name through shared
  connection settings so the notebook MCP upload workflow can resolve it.

* 2026-09-15: Add the self-contained `test00` notebook example for validating
  local notebook content before an explicitly authorized AI DP upload.

* 2026-09-15: Add the local AI DP notebook workflow MCP server for validated
  notebook upload, single-task job reconciliation, job-run submission, and
  read-only run status inspection.

* 2026-09-15: Add a documented evaluation setup for Oracle's OCI API MCP
  Server. The isolated `codex-mcp-ai-dp` Python 3.13 environment and global
  Codex stdio registration preserve the project's Python 3.11 AI DP SDK
  environment.

* 2026-09-15: Add an OCI API MCP launcher that securely selects the project
  `.env` OCI config file and profile without storing credentials in Codex.

* 2026-09-15: Remove the OCI API MCP Codex registration at the user's request;
  the isolated evaluation environment remains available but inactive.

* 2026-09-15: Add a read-only catalog tree command with paginated schema/volume
  discovery and dedicated documentation. Extract shared connection settings,
  API-key authentication, SDK initialization and discovery into `aidp_common`.

* 2026-09-15: Preserve Workbench timestamp values when the service returns
  numeric dates, avoiding an OCI SDK deserialization TypeError during discovery.
  Add execution-stage and code-location diagnostics for unexpected errors.

* 2026-09-15: Replace manual Workbench REST calls with Oracle AI DP SDK 4.2.1
  clients and typed models. Pin compatible OCI 2.165.1, document SDK installation
  and shared dependencies, and exercise generated clients with offline HTTP tests.

* 2026-09-15: Add operation banners with UTC start/end timestamps and elapsed
  time to cluster lifecycle commands. Add Python 3.11+, Black, Pylint and pytest
  badges to the main README and move contributor checks to DEVELOPMENT.md.

* 2026-09-15: Centralize `.env`, its example, and runtime/development requirements
  in the repository root for reuse across features. Preserve local settings and
  resolve the default dotenv path independently of the working directory.

* 2026-09-15: Add a spec-driven OCI AI DP cluster lifecycle script with discovery,
  status, dry-run, optional polling, regional endpoint derivation, `.env`
  configuration, dedicated documentation and offline tests. Black, Pylint and
  pytest are the required Python quality tools. Live OCI verification is pending.
