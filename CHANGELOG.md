# Changelog

## Unreleased

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
