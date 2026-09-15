# Shared infrastructure specification

## Scope and contract

Extract the existing cluster authentication and client initialization into
`aidp_common`, as required by the catalog-tree specification written before
implementation. Reuse it from both commands without feature-to-feature imports.
No new authentication modes, dependencies or remote mutations are introduced.

Preserve API-key profile selection, root dotenv resolution and precedence,
region-derived SDK endpoints, safe endpoint overrides, compartment boundaries,
active-instance discovery, timeout settings, timestamp compatibility and session
cleanup. Keep lifecycle settings and mutation behavior in `cluster_lifecycle`.
Catalog commands must not validate or use lifecycle-only settings.

Shared public helpers must explain arguments, results and relevant failure modes.
Do not expose credentials or raw SDK errors. Retain only a sanitized exception
type, execution stage and innermost code location for unexpected failures.

## Acceptance and verification

Run both commands directly by path, including from outside the repository.
Existing cluster tests must pass unchanged in behavior. Exercise real API-key
signer initialization with a generated temporary test key, token-profile rejection,
and cleanup when a later client constructor fails. All default tests must block
network traffic. Record final results in the catalog-tree specification.

The installed Oracle AI DP SDK 4.2.1 and OCI 2.165.1 source were inspected on
2026-09-15. See [Oracle's SDK repository](https://github.com/oracle-samples/aidataplatform-sdk).
Remote execution inside an OCI AI DP runtime remains a separate verification step.
