# External-volume exploration MCP tools

## Problem and scope

Codex needs a read-only way to discover external AI Data Platform (AI DP)
volumes backed by OCI Object Storage and browse their folder and file hierarchy.
The existing `catalog_tree` command lists catalog metadata but is not available
through the MCP server and does not list volume files.

Add two read-only tools to `aidp_mcp`:

| Tool | Purpose |
| --- | --- |
| `list_catalog_volumes` | Resolve one exact catalog name and list its visible schemas and volumes. By default return only external volumes; callers can include managed volumes explicitly. |
| `list_volume_files` | Resolve one volume by its catalog, schema and volume display names and return a bounded recursive hierarchy below one absolute volume path. |

No tool creates, changes, downloads, uploads, deletes, refreshes, or generates a
PAR for any resource. The tools do not read file content.

## Assumptions and prerequisites

The configured OCI API-key authentication, region, compartment, and optional
AI DP instance setting identify exactly one active, visible AI DP instance.
The caller must have visibility of the catalog and schema, and AI DP `READ`
permission on the volume. The server returns only resources visible to that
identity; this is not a tenancy-wide inventory or a transactional snapshot.

An external volume is AI DP metadata pointing to an existing Object Storage
location. Its data lifecycle remains customer-managed. `storage_location` is
returned only when AI DP reports an external volume.

## Interfaces and behavior

### `list_catalog_volumes`

Inputs:

* `catalog_name`: required exact, case-sensitive catalog display name.
* `external_only`: optional boolean, default `true`. When true, omit managed
  volumes; when false, return both types.
* `max_results`: optional integer from 1 through 1000, default 100.

The tool resolves exactly one visible catalog with that name, lists every
visible schema, and lists its volumes using the schema key returned by AI DP.
It obtains each candidate volume's detail before applying the external filter,
because the volume list summary does not provide `volume_type` or
`storage_location`. The response contains only schemas with returned volumes,
sorted deterministically by display name, and each volume includes its opaque
key, fully qualified name, type, lifecycle state and external storage location
when applicable. The server stops after detecting more than `max_results`
matching volumes and sets `is_truncated`; it must never claim a complete result
when the cap was reached.

### `list_volume_files`

Inputs:

* `catalog_name`, `schema_name`, and `volume_name`: required exact,
  case-sensitive display names. A volume name alone is insufficient because it
  is unique only within a schema.
* `path`: optional absolute POSIX volume path, default `/`; it must contain no
  `.` or `..` components.
* `max_results`: optional integer from 1 through 1000, default 100.

The tool resolves the catalog, schema and volume to an opaque volume key, then
calls `VolumeClient.list_files` with the public volume-relative `path`,
`is_recursive=true`, `sort_by="displayName"` and `sort_order="ASC"`. Some AI DP
responses return only direct children despite that flag; the tool explicitly
inspects any returned folder with no returned descendants, producing the
documented recursive tree. AI DP may
return paths with a `/Volumes/<catalog>/<schema>/<volume>` mount prefix; the
tool removes that prefix, so callers never need to know it. It also accepts
already volume-relative returned paths. It returns a `root`
tree with `FOLDER` and `FILE`
nodes, each containing only `display_name`, `path`, `type`, `time_created` and
`time_updated`. It uses paths to construct missing intermediate folder nodes;
such nodes are marked `inferred: true`. It returns no file data, metadata,
ETags, tags, creator identities, descriptions, or Object Storage URLs.

The tool follows pagination only until the bounded result count is reached.
`is_truncated` is true whenever the returned tree may omit entries. Returned
paths outside the requested root or containing traversal components are treated
as a service error rather than being emitted.

## API design and permissions

The implementation uses the installed generated AI DP SDK 4.2.1:

1. `CatalogClient.list_catalogs(instance_id, display_name=...)` to locate the
   catalog.
2. `SchemaClient.list_schemas(instance_id, catalog_key)`.
3. `VolumeClient.list_volumes(instance_id, catalog_key, schema_key)` and
   `VolumeClient.get_volume(instance_id, volume_key)`.
4. `VolumeClient.list_files(instance_id, volume_key, path, is_recursive=True)`.

Oracle's REST documentation confirms that volume listing requires catalog and
schema keys, detailed volume data contains `volumeType` and `storageLocation`,
and file listing supports an absolute path, recursive traversal and pagination.
Oracle documents volume `READ` as permitting folder/file listing and reading.
Sources verified 2026-09-16:

* [Get volumes](https://docs.oracle.com/en/cloud/paas/ai-data-platform/aiwap/op-aidataplatforms-aidataplatformid-volumes-get.html)
* [Get volume details](https://docs.oracle.com/en/cloud/paas/ai-data-platform/aiwap/op-aidataplatforms-aidataplatformid-volumes-volumekey-get.html)
* [Get files in volume](https://docs.oracle.com/en/cloud/paas/ai-data-platform/aiwap/op-aidataplatforms-aidataplatformid-volumes-volumekey-files-get.html)
* [Permissions model](https://docs.oracle.com/en/cloud/paas/ai-data-platform/aidug/permissions-model.html)

## Acceptance and verification

* Offline tests cover exact resource resolution, external-only filtering,
  bounded pagination, mount-path translation, sanitized summaries, unsafe input
  rejection, tree construction and malformed remote paths.
* The MCP schema exposes exactly the two documented tools and their default
  arguments without cloud access.
* Run Black, Pylint and pytest in the `codex-4-oci-aidp` Conda environment.
* Remote verification is intentionally pending until the user tests a new
  Codex session against a chosen catalog and volume. Record the target only in
  a later sanitized evidence update.

## Verification evidence

Implemented locally on 2026-09-16. Offline tests cover resource resolution,
external-volume filtering, recursive tree construction, result limits and safe
path validation. In the `codex-4-oci-aidp` Conda environment, Black check
passed for `aidp_mcp`, Pylint scored 10.00/10 for `aidp_mcp`, and the full test
suite passed: 147 tests on Python 3.11.0. `git diff --check` also passed.

2026-09-16 correction: live exploration showed that AI DP returns file paths
under `/Volumes/<catalog>/<schema>/<volume>`, even though this tool's contract
defines its public paths relative to the selected volume. The implementation
normalizes that response prefix but passes the public path unchanged to the
volume-specific listing endpoint. Offline regression coverage verifies that
`/datasets` is sent unchanged and that both mount-prefixed and volume-relative
responses are exposed as `/datasets/...`. Remote acceptance remains pending
until the MCP process is reloaded.

2026-09-16 regression fix verification: targeted `aidp_mcp` tests passed
(74 tests); Black check passed; Pylint scored 10.00/10; and the full local
suite passed (149 tests) on Python 3.11.0. `git diff --check` passed. Remote
verification remains pending until a new Codex MCP session is created.

2026-09-16 root-traversal fix: live read-only exploration of
`fine_tuning.fine_tuning.vol_finetuning` showed that listing `/` returned only
its three direct folders while listing `/datasets` returned five JSONL files.
The traversal now explicitly expands folders lacking returned descendants.
The new offline regression test covers this shallow-response behavior. Black
check passed, Pylint scored 10.00/10, `git diff --check` passed, and the full
local suite passed (150 tests) on Python 3.11.0. Remote verification remains
pending until Codex restarts the registered stdio MCP server.
