# Catalog tree

List the visible schemas and volumes in an OCI AI DP catalog using the
[Oracle AI DP Python SDK](https://github.com/oracle-samples/aidataplatform-sdk).
This command reads metadata only and does not start a cluster.

## Setup and usage

Use Python 3.11+ and the shared dependencies described in the
[root README](../README.md). No additional packages are needed.
Set the connection values in the root `.env`:

```dotenv
REGION=eu-frankfurt-1
OCI_CONFIG_FILE=~/.oci/config
OCI_PROFILE=DEFAULT
COMPARTMENT=your-compartment-name-or-ocid
AIDP_INSTANCE_ID=
AIDP_ENDPOINT=
```

Use an OCI API-key profile with access to discover the compartment and active
AI DP instances, and read the target catalog, schemas and volumes. Supplying a
compartment OCID avoids compartment-name discovery. `AIDP_INSTANCE_ID` optionally
restricts discovery to one active instance in that compartment. The SDK derives
the endpoint from `REGION`; normally leave `AIDP_ENDPOINT` empty.

From the repository root:

```bash
conda activate codex-4-oci-aidp
python catalog_tree/catalog_tree.py fine_tuning
```

Example output (illustrative names):

```text
fine_tuning
├── datasets
│   ├── training
│   └── validation
└── experiments
```

The catalog name is an exact, case-sensitive positional argument. Workspace and
cluster settings in `.env` are ignored. The default `.env` is resolved relative
to the repository even when the script is launched from another directory.
CLI options override environment variables, which override `.env` and defaults:

```bash
python catalog_tree/catalog_tree.py fine_tuning --region eu-frankfurt-1
python catalog_tree/catalog_tree.py fine_tuning --env-file /path/to/settings.env
python catalog_tree/catalog_tree.py --help
```

Schemas and volumes are sorted by name, including empty schemas. All pages are
retrieved before printing the tree. Only resources visible to the configured
user can be listed; the result is not a transactional snapshot during concurrent
catalog changes. Control characters in names are escaped.

Timing banners and errors go to stderr; stdout contains only the completed tree:

```bash
python catalog_tree/catalog_tree.py fine_tuning > catalog.txt
```

## Troubleshooting

* No match: check the exact name, region, compartment and user visibility.
* Multiple matches: set `AIDP_INSTANCE_ID` to select an instance.
* HTTP 401/403/404: check the selected profile, resource visibility, permissions
  and API availability. A failed listing prints no partial tree.
* Unexpected SDK errors: diagnostics include the execution stage and code
  location without printing raw responses or credentials.

Exit codes: `0` success, `1` operational error, `2` invalid arguments,
`130` interruption. There are no resource changes or cleanup steps.

For OCI AI DP execution, copy the whole repository, install the root runtime
requirements in Python 3.11+, and provide an accessible API-key profile and
network access to OCI services. Conda is optional. Instance/resource-principal
authentication is not currently implemented.

See the [specification](specs/001-catalog-tree.md) for API details and verification
evidence, and [shared infrastructure](../aidp_common/README.md) for authentication
and client initialization.
