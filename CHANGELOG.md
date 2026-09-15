# Changelog

## Unreleased

* 2026-09-15: Centralize `.env`, its example, and runtime/development requirements
  in the repository root for reuse across features. Preserve local settings and
  resolve the default dotenv path independently of the working directory.

* 2026-09-15: Add a spec-driven OCI AI DP cluster lifecycle script with discovery,
  status, dry-run, optional polling, regional endpoint derivation, `.env`
  configuration, dedicated documentation and offline tests. Black, Pylint and
  pytest are the required Python quality tools. Live OCI verification is pending.
