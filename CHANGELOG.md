# Changelog

## Unreleased

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
