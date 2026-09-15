# OCI AI DP cluster lifecycle

Find a Workbench cluster by compartment and exact name, then inspect, start or
stop it. Python uses the OCI SDK for authentication and instance discovery, and
the [Oracle AI DP SDK](https://github.com/oracle-samples/aidataplatform-sdk) for
Workbench operations through `WorkspaceClient` and `ClusterClient`. OCI CLI is
not required.

See the [specification](specs/001-cluster-lifecycle.md).

## Configure once in `.env`

Edit the repository root `.env`, which is excluded from Git. A ready-to-fill file
is provided locally; for a fresh checkout, copy [.env.example](../.env.example):

```bash
cp .env.example .env
```

Do not overwrite an existing configured file. Start with:

```dotenv
REGION=eu-frankfurt-1
COMPARTMENT=your-compartment-name-or-ocid
CLUSTER_NAME=your-cluster-name
WORKSPACE_NAME=your-workspace-name
OCI_CONFIG_FILE=~/.oci/config
OCI_PROFILE=DEFAULT
```

The default `.env` is always located in the repository root, regardless of the current
working directory. Use `--env-file /path/to/settings.env` for a different file.
Values are literal: dotenv interpolation such as `${OTHER_VARIABLE}` is disabled.
Use quotes around values containing spaces or `#` when needed.

Settings precedence is **CLI > process environment > `.env` > defaults**. Empty
optional settings are unset. Existing process variables are never modified.

| Variable | Default / purpose |
| --- | --- |
| `REGION` | `eu-frankfurt-1`; used by both OCI discovery and Workbench |
| `COMPARTMENT` | Required: compartment OCID or exact name; use tenancy OCID for root |
| `CLUSTER_NAME` | Required: exact, case-sensitive display name |
| `WORKSPACE_NAME` | Optional exact workspace display name |
| `WORKSPACE_KEY` | Optional workspace key; mutually exclusive with workspace name |
| `AIDP_INSTANCE_ID` | Optional instance OCID to narrow discovery |
| `CLUSTER_TYPE` | Empty for both types, `USER` for Spark or `AI_COMPUTE` |
| `OCI_CONFIG_FILE` | `~/.oci/config`; API signing key stays at its configured path |
| `OCI_PROFILE` | `DEFAULT`; API-key profiles only |
| `ACTION` | `status`; positional CLI action overrides it |
| `WAIT` | `true` in the supplied template; code fallback is `false` |
| `WAIT_TIMEOUT` | `1200` seconds |
| `POLL_INTERVAL` | `10` seconds |
| `DRY_RUN` | `false`; read-only validation when true |
| `AIDP_ENDPOINT` | Normally empty; optional HTTPS origin override |

The installed AI DP SDK derives its endpoint from `REGION` and OCI realm metadata.
Frankfurt resolves to `https://datalake.eu-frankfurt-1.oci.oraclecloud.com`.
An explicit `AIDP_ENDPOINT` is passed as the SDK's `service_endpoint` override.
The SDK uses Workbench API version `20260430`.

## Environment and packages

Use Python 3.11+ and the project Conda environment. Follow the root
[installation instructions and dependency table](../README.md#shared-configuration-and-dependencies)
to download the Oracle SDK wheel and install shared requirements. Runtime packages
are `oci`, `aidp-python-client` and `python-dotenv`. The SDK handles signing,
resource URL construction, serialization and typed responses.

The script is intended for macOS and a Python-capable OCI AI DP runtime with
dependencies, API-key configuration and network access. Conda is not required
remotely. Resource-principal and session-token authentication require a separate
specification.

## Run

After filling `.env`, run from the repository root:

```bash
python cluster_lifecycle/cluster_lifecycle.py status
python cluster_lifecycle/cluster_lifecycle.py start --dry-run
python cluster_lifecycle/cluster_lifecycle.py start --wait
python cluster_lifecycle/cluster_lifecycle.py stop --wait
```

Override any configured target on the command line when needed:

```bash
python cluster_lifecycle/cluster_lifecycle.py status \
  --compartment 'another-compartment' --cluster-name 'another-cluster'
```

`--no-wait` and `--no-dry-run` explicitly override true settings. With no action,
`ACTION` is used (the supplied template selects `status`). Run `--help` for flags.

An opening `###` banner shows the operation and start time in UTC. A matching
closing banner shows the end time and elapsed seconds, including on failure or
interruption. Dry-run operations are labeled explicitly. Times describe script
execution; an accepted cloud operation may still be running.

The following output record identifies instance OCID, workspace key, cluster key,
current state and action. Without waiting, a successful submission reports
**accepted**, not completed. With waiting, success reports the observed final
state. Exit codes: `0` success/no-op/dry-run/accepted request; `1` operational
failure; `2` invalid settings; `130` interruption.

## Discovery and permissions

Discovery searches active instances in the compartment and region, then visible
workspaces and clusters, following all result pages. It requires exactly one
match. Supply instance/workspace/type selectors to resolve ambiguity. An
explicit instance must still belong to the selected compartment. Failed list
requests abort discovery; uniqueness can only be checked among visible resources.
Using a workspace key together with its instance OCID avoids probing unrelated
instances for that key.

The OCI user needs access to inspect AI DP instances and Workbench permissions
to list/read the selected workspaces and clusters and perform lifecycle actions.
Compartment-name lookup also needs IAM compartment discovery. Ask the tenancy
administrator to map these operations to the deployed IAM policies and Workbench
roles. The script does not grant permissions. API-key registration and network
access to OCI and Workbench endpoints are prerequisites.

## State handling and recovery

| Action | Current state | Behavior |
| --- | --- | --- |
| start | STOPPED | Submit start |
| start | ACTIVE | Successful no-op |
| start | STARTING | Do not resubmit; optionally wait |
| stop | ACTIVE | Submit stop |
| stop | STOPPED | Successful no-op |
| stop | STOPPING | Do not resubmit; optionally wait |
| either | Other state | Fail and ask the operator to inspect the cluster |

Dry-run performs real read requests and state validation. It cannot prove
mutation permissions, available capacity or future state stability. ETags guard
against concurrent changes when available. Mutations use an explicit
`NoneRetryStrategy` and a unique retry token; the application does not resubmit
failed actions. OCI pagination may retry reads, and SDK transport recovery may
retry malformed HTTP-header responses using the same token. HTTP redirects are
disabled. HTTP connect/read timeouts are 10/30 seconds. An in-flight HTTP request
may extend the polling deadline.

Start can incur compute charges. Stop can interrupt attached workloads and does
not guarantee that associated storage/service charges cease. Coordinate stop
with users of the cluster. No resources are created or deleted by this script.

A lost submission response leaves the action's outcome unknown: run `status`
before retrying. Wait timeout or Ctrl-C does not cancel cloud operations.
Recovery is an explicit status check, service-side investigation and an
appropriate authorized action. No automatic rollback or cleanup runs.

## Troubleshooting

* Missing target: fill `COMPARTMENT` and `CLUSTER_NAME` in `.env`.
* No/multiple matches: check region, exact names, active instances, visibility
  and optional instance/workspace/type selectors.
* HTTP 401/403: check API-key profile, registration, clock and permissions.
* HTTP 404: check API availability, identifiers, visibility and regional routing;
  `AIDP_ENDPOINT` can override the origin if deployment documentation requires it.
* HTTP 409/412: inspect status; another operation may have changed the resource.
* HTTP 429 or transport failure: check status and retry later as appropriate.
* Invalid response: check that the deployment supports API version `20260430`.

Errors omit raw response bodies, signing headers, key contents and configuration
values. Sanitize resource metadata before sharing logs publicly.

## Remote verification

For an explicitly authorized live test, record a non-production cluster's
initial state, inspect status and dry-run, then start/stop with waiting as
appropriate. Record runtime/package versions, region, cluster type, sanitized
outputs and final state in the specification. Restore the initial state only
when authorized. Live start/stop validation is still pending; the instance-level default
cluster is outside this feature's scope.
