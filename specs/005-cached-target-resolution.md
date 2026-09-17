# Cached AI DP target resolution for MCP tools

## Problem and scope

Every `aidp_mcp` tool call creates a new `AidpWorkflowService` and enters
`_clients()` or `_catalog_clients()`. Before performing the requested
operation, each entry repeats the same discovery sequence against OCI:

| Step | Current call | Cost |
| --- | --- | --- |
| `resolve_compartment` | `IdentityClient.list_compartments` over the whole tenancy subtree, paginated | One or more calls to the home-region Identity service; skipped only when `COMPARTMENT` is an OCID |
| `list_instances` | `AiDataPlatformClient.get_ai_data_platform` | One call; skipped only when `AIDP_INSTANCE_ID` is empty, which instead lists instances |
| `find_workspace` | `WorkspaceClient.list_workspaces` filtered by display name | One call; workspace-scoped tools only |

With the project's current `.env` (compartment given by name, instance OCID
set, workspace name set) this is at least three remote calls per tool call,
before any useful work. The results are stable identifiers that do not change
during an MCP session. A Codex session issuing ten tool calls therefore spends
roughly thirty avoidable round trips.

This specification reduces the steady-state discovery cost to zero remote calls.
For a name-based compartment and an explicit instance, the first workspace
call still makes three remote requests (`get_ai_data_platform`,
`get_compartment`, and `list_workspaces`); the improvement is replacing a
tenancy-wide, paginated listing with one targeted lookup. It changes no tool
signature, output shape, safety gate, or authentication behavior.

Scope:

1. Cache the resolved target (`instance_id`, and `workspace_key` when needed)
   for the lifetime of the MCP server process, keyed by the settings that
   determine it.
2. When `AIDP_INSTANCE_ID` is set and `COMPARTMENT` is a name, validate the
   compartment with one `IdentityClient.get_compartment` call on the instance's
   own compartment instead of listing the tenancy subtree.
3. Document that supplying the compartment OCID in `.env` avoids the Identity
   call entirely.

Non-goals:

* Caching SDK clients, HTTP sessions, signers, or credentials. Clients remain
  short-lived per request; only two opaque identifiers are retained.
* Caching any tool result, resource listing, cluster state, or job state.
* Time-based expiry or background refresh.
* Changing the `cluster_lifecycle` or `catalog_tree` commands, which are
  one-shot processes and gain nothing from a cache.

## Assumptions and prerequisites

The MCP server runs as one long-lived stdio child process per Codex session,
as documented in `aidp_mcp/README.md`. Instance OCIDs and workspace keys are
stable for the life of those resources. The configured settings continue to
identify exactly one active AI DP instance and one workspace.

## Interfaces and behavior

### Resolved target cache

Add a module-level cache in `aidp_mcp/service.py`:

* The cache key is the tuple of the settings and tenancy that determine the
  target: `config_file`, `profile`, `region`, `compartment`, `instance_id`,
  `workspace_name`, `endpoint`, and the tenancy OCID read from the selected
  OCI profile. Because `load_connection_settings()` still reads `.env` on
  every request, an edit to a setting that is not overridden by the server
  process environment changes the key and triggers fresh resolution without a
  restart. Environment variables take precedence over `.env`, as they do
  today.
* The cached value holds `instance_id` and an optional `workspace_key`.
  Catalog tools need only the instance; workspace tools need both. A catalog
  call may populate the instance part, and a later workspace call adds the
  workspace part without re-resolving the instance.
* Access and a cache miss are guarded by one `threading.Lock`. FastMCP runs
  synchronous tool functions in worker threads, so concurrent calls for the
  same key perform one discovery and receive the same stored target. The lock
  is deliberately held through discovery: target resolution is infrequent and
  bounded by the existing SDK timeouts. It can serialize simultaneous misses
  for distinct keys, which is acceptable for one local MCP process.
* Expose `clear_target_cache()` for tests and for the invalidation rule below.

### Resolution flow

`_clients()` and `_catalog_clients()` share one helper, for example
`_resolve_target(settings, *, need_workspace)`:

1. Look up the key. On a hit that satisfies `need_workspace`, return it and
   construct only the Workbench clients the tool needs. Do not construct the
   Identity or control-plane clients on a hit; they exist only for discovery.
2. On a miss, perform discovery exactly as today, except for the compartment
   shortcut below, then store the result and return it. Do not store a partial
   result when discovery raises.

### Compartment validation shortcut

`aidp_common.connection.list_instances` currently receives an already resolved
compartment OCID. Introduce the cheaper path without breaking the
`cluster_lifecycle` and `catalog_tree` callers of `resolve_compartment`:

* If `COMPARTMENT` starts with `ocid1.compartment.` or `ocid1.tenancy.`,
  behavior is unchanged: no Identity call.
* If `COMPARTMENT` is a name and `AIDP_INSTANCE_ID` is set: call
  `get_ai_data_platform` first, then `IdentityClient.get_compartment` with the
  instance's `compartment_id`. Accept the instance when that compartment is
  `ACTIVE` and its `name` equals the configured value exactly. Otherwise raise
  `AidpError` with the same actionable wording used today.
* If `COMPARTMENT` is a name and `AIDP_INSTANCE_ID` is empty: behavior is
  unchanged, because listing instances requires the compartment OCID first.

Accepted behavior change: today a compartment name that matches two
compartments anywhere in the tenancy is rejected as ambiguous even when the
selected instance sits in one of them. With the shortcut, the instance's own
compartment decides. Record this in the CHANGELOG entry.

### Invalidation

If a tool call fails with an `oci.exceptions.ServiceError` whose status is 404
after consuming a complete cached target, drop that cache entry before
re-raising. Implement this once in the `_clients()` and `_catalog_clients()`
context-manager exception path. The failure still surfaces to the caller
unchanged; the next call re-resolves. A 404 caused by an absent notebook or job
triggers one unnecessary re-resolution on the following call, which is
acceptable and simpler than classifying errors.

The cache does not revalidate a previously selected instance's lifecycle or
permissions on every hit. An instance moved, made inactive, or made
inaccessible can therefore fail in the requested tool operation; only a 404
causes automatic invalidation. This is an intentional process-lifetime cache
trade-off, not evidence that the target remains valid.

### Documentation

* `aidp_mcp/README.md`: one short paragraph stating that the server caches the
  resolved instance and workspace identifiers for the process lifetime, that
  applicable `.env` edits are picked up automatically (unless overridden by
  the process environment), and that a deleted or renamed workspace surfaces
  as a clear error on the next call.
* `.env.example`: comment on `COMPARTMENT` recommending the OCID for fewer
  OCI calls, keeping the name form supported.
* `CHANGELOG.md`: one `Unreleased` entry dated on the implementation day.

## API design and permissions

No new AI DP or OCI operation is introduced beyond
`IdentityClient.get_compartment`, which requires `inspect compartments` on the
target compartment. The identity that can already run `list_compartments`
with `access_level=ACCESSIBLE` over the subtree can read a single compartment
it has access to. Sources verified 2026-09-17:

* [IdentityClient.get_compartment](https://docs.oracle.com/en-us/iaas/tools/python/latest/api/identity/client/oci.identity.IdentityClient.html#oci.identity.IdentityClient.get_compartment)
* [AiDataPlatformClient.get_ai_data_platform](https://docs.oracle.com/en-us/iaas/tools/python/latest/api/ai_data_platform/client/oci.ai_data_platform.AiDataPlatformClient.html#oci.ai_data_platform.AiDataPlatformClient.get_ai_data_platform)

Both methods exist in the pinned `oci` 2.165.1 SDK, and
`oci.identity.models.Compartment` exposes the `name` and `lifecycle_state`
fields the shortcut compares.

## Acceptance and verification

Offline tests in `aidp_mcp/tests/test_service.py`, with an autouse fixture
that calls `clear_target_cache()` so existing tests that patch
`resolve_compartment`, `list_instances`, and `find_workspace` stay isolated:

* Two consecutive workspace tool calls with identical settings perform
  discovery once; the second call makes zero calls to the patched discovery
  functions and does not construct Identity or control-plane clients.
* A catalog call followed by a workspace call resolves the instance once and
  the workspace once.
* Changing any key field, for example `workspace_name`, triggers a new
  resolution.
* A `ServiceError` with status 404 raised inside a tool clears the entry and
  propagates; the following call resolves again.
* With a compartment name and an instance OCID, resolution calls
  `get_compartment` once and never `list_compartments`; a name mismatch or a
  non-active compartment raises `AidpError`.
* With a compartment OCID, no Identity call is made, as today.
* Two threads entering resolution concurrently perform discovery once and
  receive one cached target.
* A discovery failure leaves no cache entry.

Quality gates: Black, Pylint 10.00/10, and the full pytest suite in the
`codex-4-oci-aidp` Conda environment; `git diff --check` clean.

Remote verification, explicit and opt-in: in a new Codex session call
`get_cluster_status` twice for the same cluster and compare wall-clock times
reported by the client, or enable OCI SDK request logging and confirm the
second call contains only the `get_cluster` request. Record sanitized timings
in the evidence section; do not record OCIDs.

## Verification evidence

Implemented locally on 2026-09-17.

* `conda run -n codex-4-oci-aidp pytest` passed: 157 tests.
* `conda run -n codex-4-oci-aidp pylint aidp_mcp/service.py
  aidp_mcp/tests/test_service.py` passed with 10.00/10.
* Black completed without further changes and `git diff --check` passed.
* The offline tests cover cache hits, catalog-to-workspace promotion, key
  changes, concurrent misses, failed discovery, cached-target 404
  invalidation, and the explicit-instance named-compartment lookup.

Remote verification remains pending and must be run explicitly against an
authorized AI DP target. No OCI resource was created, changed, or deleted by
this implementation.
