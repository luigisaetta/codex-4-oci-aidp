# Shared OCI AI DP infrastructure

Both feature scripts import this package directly from the repository root.
Keep the whole repository available when running either script; there is no
separate package installation or per-feature dependency file.

| Module | Responsibility |
| --- | --- |
| `settings.py` | Root dotenv loading, connection arguments and validation |
| `connection.py` | API-key signing, managed SDK sessions, compartment/instance discovery |
| `output.py` | Timing banners and sanitized unexpected-error diagnostics |

Feature modules own their operations and feature-specific settings. They must
not import each other. New commands should reuse `connection_parser` and
`validate_connection`, then call `load_auth` and create clients through
`managed_client` inside an `ExitStack` so sessions close on success and failure.

Pass `preserve_timestamps=True` for Workbench clients whose timestamps are not
interpreted by the command. This preserves numeric or string timestamps without
guessing their units, using a per-client copy of OCI's type mapping. Control-plane
clients retain normal date parsing. HTTP redirects are disabled and connection/read
timeouts are 10/30 seconds. List pagination uses OCI's bounded default read retry
strategy; cluster mutations retain their explicit no-retry strategy.

Configuration and dependencies remain in the root files. See the
[shared specification](specs/001-shared-infrastructure.md) and
[development guide](../DEVELOPMENT.md).
