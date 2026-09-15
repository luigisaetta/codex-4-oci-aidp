# Catalog tree

## Purpose and scope

Provide `python catalog_tree/catalog_tree.py <catalog-name>` to print all visible
schemas and their volumes as a tree. Use the installed Oracle AI DP SDK 4.2.1.
Read connection settings from the shared root `.env`. No new dependencies,
resource changes, file downloads, cluster operations or catalog refreshes.

## Shared infrastructure

Create a root `aidp_common/` package for API-key authentication, SDK client setup,
resource-key validation, compartment/instance discovery, dotenv connection
settings, safe diagnostics and execution banners. Both features depend on this
package; neither feature imports the other. Keep feature settings and behavior
in their own folders. Direct script execution must work outside the repository
working directory without an editable installation.

Preserve cluster lifecycle options, exit codes, timestamp compatibility, client
cleanup, no-redirect policy and mutation retry behavior. Shared Workbench client
setup preserves opaque timestamps with a private copy of the SDK type mapping;
control-plane clients retain normal timestamp parsing. Defaults are API-key
profile DEFAULT, `~/.oci/config`, region eu-frankfurt-1, HTTP timeouts 10/30s.

## Inputs and discovery

The catalog name is a required positional argument, matched exactly. Shared
settings: COMPARTMENT, REGION, OCI_CONFIG_FILE, OCI_PROFILE, AIDP_INSTANCE_ID,
AIDP_ENDPOINT and --env-file. CLI overrides environment, then root `.env`, then
defaults. Ignore lifecycle-specific settings, including WORKSPACE_NAME,
WORKSPACE_KEY, CLUSTER_NAME, ACTION and WAIT. Workspace scope is not part of
the catalog API.

Resolve the compartment and active instances with OCI. If an instance OCID is
supplied, verify its compartment and state. Use CatalogClient.list_catalogs with
display_name, then require exactly one visible matching catalog across the
selected instances. Follow every page; missing/ambiguous names are errors.

Use SchemaClient.list_schemas(instance_id, catalog_key). For each schema, call
VolumeClient.list_volumes(instance_id, catalog_key, schema_key). Pass keys
returned by the SDK, including fully qualified schema keys; do not construct
them from display names. Follow all pages at both levels. The listing includes
only resources visible to the configured OCI user. Any list/permission failure
must abort with a nonzero status rather than produce a misleading complete tree.

## Output

Gather the complete tree before printing it. Sort schemas and volumes by display
name, with deterministic tie-breaking by key. Print an empty catalog as its name
alone and retain schemas that contain no volumes. Escape control characters in
labels so remote names cannot inject terminal commands or extra tree lines.

```text
my_catalog
├── schema1
│   ├── volume1
│   └── volume2
└── schema2
```

Use `###` opening/closing banners with operation, UTC start/end and monotonic
elapsed time. Banners go to stderr so stdout is just the tree. Errors identify
the failed stage without raw SDK bodies or credentials. Exit codes: 0 success,
1 operational failure, 2 invalid arguments, 130 interruption.

## Acceptance and verification

* Existing cluster tests pass after shared-code extraction.
* Tests exercise real generated SDK clients with replaced HTTP transport for
  catalog/schema/volume pagination, numeric timestamps, qualified schema keys,
  empty results, exact matching, duplicate names and service failures.
* Test deterministic rendering and control-character escaping, and ensure no
  partial tree is printed when any listing fails.
* Verify root dotenv location and that catalog settings do not require cluster
  or workspace values. Test shared API-key initialization and session cleanup.
* Run Black, Pylint and pytest in codex-4-oci-aidp. Record results here, not in README.
* Remote catalog verification needs a target name; record it separately from
  offline tests. Never infer full tenancy visibility from a successful listing.

## Evidence

SDK 4.2.1 installed client signatures and model metadata inspected on 2026-09-15:
CatalogClient.list_catalogs, SchemaClient.list_schemas, VolumeClient.list_volumes.
Source: [Oracle SDK](https://github.com/oracle-samples/aidataplatform-sdk/tree/v4.2.1).
CatalogCollection, SchemaCollection and VolumeCollection expose paginated items.
Live read-only verification on 2026-09-15 from macOS, using the project Conda
environment and root `.env` in eu-frankfurt-1, succeeded for the user-selected
catalog `fine_tuning` (exit 0, 2.998 seconds). Two schemas were visible: `default`
(empty) and `fine_tuning`, containing volume `vol_finetuning`. No cloud resources
were changed. This confirms this user's read path, not unrestricted visibility
or execution inside the OCI AI DP runtime; that runtime remains unverified.

Final local checks on 2026-09-15: Python 3.11.0, AI DP SDK 4.2.1, OCI 2.165.1;
74 pytest tests passed (including all 63 existing cluster tests), Black check
passed for 13 Python files, Pylint scored 10.00/10, and `git diff --check` passed.
Both scripts' help commands succeeded when invoked by absolute path from `/tmp`.
The live cluster `status` regression also succeeded (exit 0, state STOPPED,
2.151 seconds); no start/stop operation was performed.
