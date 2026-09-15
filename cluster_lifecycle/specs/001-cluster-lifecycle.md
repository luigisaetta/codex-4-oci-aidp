# Cluster lifecycle control

## Goal and scope

Provide a human-reviewable Python command that finds an existing OCI AI DP
Workbench cluster by compartment and exact cluster name, then starts or stops it.
Support read-only status and dry-run commands. Keep feature code, tests, and
documentation in `cluster_lifecycle/`. Share configuration and dependencies
across features through root `.env`, `.env.example`, `requirements.txt`, and
`requirements-dev.txt` files. Preserve existing local settings when moving them.

The initial implementation uses API-key authentication from `~/.oci/config`
and the key file referenced by the selected profile. It requires Python 3.11+
and `oci`, `requests`, and `python-dotenv`. Development uses Black, Pylint and
pytest. Exact direct dependencies are pinned in the repository root requirements
files. No separate AIDP SDK or OCI CLI installation is required.

## Inputs and discovery

* Required settings: compartment OCID or exact name and cluster name. Read all
  settings from the repository root `.env`, with environment variables and CLI flags
  taking precedence. Default action is `status`; default region is
  `eu-frankfurt-1`. The default `.env` location is independent of the working directory.
* Derive the Workbench endpoint using the Oracle generated ClusterClient template
  `https://datalake.{region}.oci.{secondLevelDomain}` and OCI region/realm metadata.
  Allow an optional explicit endpoint override for deployment-specific routing.
* Accept a workspace display name as well as a workspace key (mutually exclusive).
  Blank optional settings are unset; invalid booleans/numbers fail before OCI access.
* Optional: OCI config path/profile, region override, instance OCID, workspace
  name or key, cluster type (`USER` or `AI_COMPUTE`), dry-run, wait timeout and interval.
* Resolve compartment names among accessible active compartments; reject
  missing or ambiguous names. OCIDs avoid tenancy-wide name discovery.
* Search active AI DP instances in that compartment and configured region,
  then their visible workspaces and clusters. Follow pagination at each level.
* Optional instance/workspace/type filters narrow the search. An explicit
  instance must still belong to the specified compartment and be active.
* Require exactly one matching cluster across the selected scope. A failed
  discovery request aborts the operation; never silently ignore inaccessible
  scopes. Discovery can only assess resources visible to the caller.

## Behavior and safety

After settings validation, print a `###` banner with the requested operation
and start timestamp in UTC. Always print a matching end banner after execution,
including errors and interruption, with the end timestamp and elapsed seconds
measured by a monotonic clock. Identify dry-run mode in both banners. These mark
script execution, not completion of an asynchronous cloud operation. Help and
invalid arguments do not start an operation and do not print execution banners.

Print the resolved instance, workspace, cluster key and current state before
any mutation. Start only from `STOPPED`; stop only from `ACTIVE`. Treat the
requested destination state as a successful no-op. If the matching transition
is already running, do not resubmit it; optionally wait. Reject other states.

Use signed REST calls to API version `20260430`. Send an empty JSON object to
start/stop, attach the latest ETag when available, and never automatically retry
a mutation. A network failure after submission may have an unknown outcome;
instruct the operator to check status before retrying. Disable HTTP redirects.

`--dry-run` performs discovery and validates the observed state without POSTs.
It does not prove mutation permissions, capacity, or future state stability.
`--wait` polls cluster details until `ACTIVE` or `STOPPED`, fails on a terminal
failure/deletion or conflicting state, and has a configurable deadline. HTTP
calls also have finite timeouts. A timeout never cancels the cloud operation.
Without waiting, report acceptance separately from completed execution.

Never log config contents, keys, signing headers, or raw error bodies. Provide
sanitized diagnostics. No creation, deletion, IAM changes, or automatic rollback.
Starting may incur compute costs; stopping can interrupt attached workloads.
Recovery is an explicit status check followed by an appropriate authorized action.

## Acceptance criteria and verification

1. Exact-name discovery follows all pages and refuses absent/duplicate targets.
2. Profile and region selection use documented OCI authentication mechanisms.
3. Status/dry-run and repeated requests in the desired state send no mutations.
4. Start and stop use the documented method/path/body, encode path segments,
   preserve ETags, and distinguish acceptance from completion.
5. Transitional, failed, conflicting, HTTP-error and timeout paths are explicit.
6. Run tests with pytest using fake clients and responses, without credentials
   or network. Verify settings precedence, endpoint derivation and discovery.
   Format all Python with Black and pass Pylint before completion.
7. A separately authorized live test must verify discovery, start and stop in
   the target OCI AI DP deployment. Mocked tests cannot satisfy this criterion.

## Permissions and runtime assumptions

The caller needs OCI access to inspect AI DP instances in the target compartment
and Workbench permissions to list the relevant workspaces/clusters, read the
target and perform its lifecycle actions. Name-based compartment resolution
also requires IAM compartment discovery. Exact policy statements and Workbench
roles must be checked for the target tenancy; this feature does not grant them.

The same standalone script is intended for macOS and a Python-capable OCI AI DP
runtime with API-key configuration, dependencies, and network access to both
control-plane and Workbench APIs. Resource/session principal authentication and
the instance-level default cluster are outside this initial scope.

## Sources

Oracle documentation inspected on 2026-09-15:

* [OCI AI DP control-plane client](https://docs.oracle.com/en-us/iaas/tools/python/latest/api/ai_data_platform/client/oci.ai_data_platform.AiDataPlatformClient.html)
  (documentation version 2.185.2): instance discovery, not workspace cluster start/stop.
* [Workbench cluster API](https://docs.oracle.com/en/cloud/paas/ai-data-platform/aiwap/api-cluster.html):
  cluster lookup and lifecycle routes.
* [Start contract](https://docs.oracle.com/en/cloud/paas/ai-data-platform/aiwap/op-aidataplatforms-aidataplatformid-workspaces-workspacekey-clusters-clusterkey-actions-start-post.html)
  and [stop contract](https://docs.oracle.com/en/cloud/paas/ai-data-platform/aiwap/op-aidataplatforms-aidataplatformid-workspaces-workspacekey-clusters-clusterkey-actions-stop-post.html):
  request details, asynchronous acceptance, states and concurrency headers.
* [Cluster listing](https://docs.oracle.com/en/cloud/paas/ai-data-platform/aiwap/op-aidataplatforms-aidataplatformid-workspaces-workspacekey-clusters-get.html)
  and [workspace listing](https://docs.oracle.com/en/cloud/paas/ai-data-platform/aiwap/op-aidataplatforms-aidataplatformid-workspaces-get.html):
  collection shape, names, keys and pagination.
* [OCI signed raw requests](https://docs.oracle.com/en-us/iaas/tools/python/latest/raw-requests.html):
  API-key signer integration with Requests.
* [Oracle AIDP SDK and CLI](https://github.com/oracle-samples/aidataplatform-sdk):
  a separate generated client is available; this implementation avoids that
  additional installation by using the public REST contract.
* [Oracle generated ClusterClient](https://github.com/oracle-samples/aidataplatform-sdk/blob/main/aidp-python-client/src/aidp_python_client/aidataplatform_dp/cluster_client.py):
  regional endpoint template. The installed OCI SDK resolves the template with
  region/realm metadata; Frankfurt resolves to
  `https://datalake.eu-frankfurt-1.oci.oraclecloud.com`. This source-based choice
  still needs validation against the user's deployment.

## Verification record

Local verification uses the user-created `codex-4-oci-aidp` Conda environment,
Python 3.11.0 on macOS, OCI 2.186.0, Requests 2.34.2, python-dotenv 1.2.3,
Black 26.5.1, Pylint 4.0.8 and pytest 9.1.1. Black, Pylint and offline pytest
checks pass; `DEVELOPMENT.md` provides reproducible commands. Tests cover the SDK's
real pagination aggregation using fake service responses, plus configuration,
authentication wiring and lifecycle boundaries.

No OCI configuration or private keys were inspected and no live OCI calls were
made. Target permissions and deployed API compatibility remain unverified;
acceptance criterion 7 is pending. No exact Astra model identifier is available
in the session metadata. Automation produced the specification, implementation,
configuration template and tests; the user supplied requirements and created the
Conda environment. The user must fill target values before live verification.
