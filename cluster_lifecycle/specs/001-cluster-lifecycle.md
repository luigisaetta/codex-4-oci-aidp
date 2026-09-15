# Cluster lifecycle control

## Goal and scope

Find an existing OCI AI DP Workbench cluster by compartment and exact name,
then inspect, start or stop it. Keep feature code and tests in
`cluster_lifecycle/`; share root `.env`, `.env.example`, `requirements.txt` and
`requirements-dev.txt`. Preserve existing local settings and command behavior.

Use Python 3.11+, `oci`, `aidp-python-client` and `python-dotenv`. Use Oracle's
`WorkspaceClient` and `ClusterClient` for Workbench operations and typed models
for requests/responses. Use OCI IdentityClient and AiDataPlatformClient for
compartment/instance discovery. No application HTTP adapter, hand-built API URLs,
manual response decoding, or direct Requests dependency is required.

## Dependency and installation decision

Install Oracle AI DP SDK 4.2.1 from the wheel inside the official GitHub release
ZIP. Verify the ZIP SHA256:
`9cd99a1196b89e9a3354f8b9111ce59412efb23c4888e56126e3a2ca2364c3ba`.
The wheel metadata requires `oci>=2.165.0,<2.166`; pin OCI 2.165.1. Its installed
AiDataPlatformClient provides the required discovery operations. Downloaded
artifacts belong in ignored `.deps/`, never in version control. The root README
contains reproducible download/install instructions and a dependency table.

## Inputs and discovery

* Required: compartment OCID or exact name, and exact cluster display name.
* Load CLI > process environment > root dotenv > defaults. Resolve the default
  dotenv path independently of the current directory. Default action is status;
  default region is `eu-frankfurt-1`. Preserve all existing settings and flags.
* Optional selectors: instance OCID, workspace name or key (mutually exclusive),
  cluster type USER or AI_COMPUTE. Blank optional settings are unset.
* Let SDK clients resolve regional endpoints from config. Pass service_endpoint
  only for a validated explicit HTTPS origin override. The installed SDK uses
  API version `20260430`; Frankfurt resolves to
  `https://datalake.eu-frankfurt-1.oci.oraclecloud.com`.
* Use API-key authentication from the selected OCI profile and its referenced
  private key. Defaults: `~/.oci/config`, profile DEFAULT. Session-token and
  resource-principal authentication are outside this initial scope.
* Resolve compartment names among accessible active compartments; reject absent
  or duplicate names. OCIDs avoid name discovery; use the tenancy OCID for root.
* Search active AI DP instances in the compartment and region. Follow OCI
  pagination for instances, workspaces and clusters. Use exact matches and require
  one unique cluster across the visible selected scope. Discovery errors abort
  the operation; never skip inaccessible scopes silently.
* Validate explicitly selected instances against the compartment and active state.
  Restrict resource keys to nonempty single path segments: OCI 2.165.1 preserves
  slash separators in path parameters. Reject slash/backslash and dot segments;
  let SDK encoding handle other characters. Display names are unaffected.

## Execution behavior

### Numeric timestamp compatibility

Live status on 2026-09-15 revealed an integer `ClusterSummary.timeCreated` in
the Workbench response. OCI 2.165.1 tries to parse it as an ISO string and raises
TypeError. Lifecycle operations do not interpret these timestamps. For the two
Workbench clients only, copy the SDK type mapping and map `datetime` to `object`
to preserve timestamp values verbatim (numbers, strings or null). Keep typed
resource models and normal OCI control-plane parsing. Do not guess timestamp
units or patch installed SDK files or global mappings. Test numeric and ISO
timestamps through the real SDK transport/deserialization path.

Unexpected errors should identify the execution stage and the innermost code
location without printing exception values, response bodies or local variables.

Live verification: reproduced the TypeError in OCI's `__deserialize_datetime`
while listing clusters, confirmed that `ClusterSummary.timeCreated` was an int,
then reran status with this compatibility setting. It completed with exit code 0
and state STOPPED on 2026-09-15. Resource identifiers and timestamp values were
not recorded. Only read operations were executed; start/stop remains unverified.

After validating settings, print a `###` start banner with the requested action,
dry-run marker when applicable, and UTC start time. Always print a final banner
with UTC end time and elapsed seconds measured by a monotonic clock, including
failure and interruption. Help and invalid settings do not start an operation.
Banners describe script execution, not completion of asynchronous cloud work.

Read cluster details via `get_cluster`. Print resolved identifiers and current
state before mutation. Start only from STOPPED; stop only from ACTIVE. The
requested destination state is a successful no-op. A matching STARTING/STOPPING
transition is not resubmitted and can optionally be awaited. Reject other states.

Submit `start_cluster` with `StartClusterDetails()` or `stop_cluster` with
`StopClusterDetails()`. Forward the current ETag as if_match when available.
Use an explicit NoneRetryStrategy and a unique opc_retry_token for each action.
The application does not retry mutations. OCI pagination can retry read calls;
OCI transport recovery can retry malformed HTTP-header responses, preserving
that request's retry token. Refuse redirects through session.max_redirects=0.
Close client HTTP sessions after execution.

Dry-run performs real discovery and state checks, but no mutation. It cannot
prove action permissions, capacity or future state stability. Require the
expected 202 action response and report acceptance separately from completion.
Transport failure during submission has an unknown outcome: instruct the user
to check status before retrying. Do not expose raw SDK errors, credentials,
signed requests, config values or response bodies.

Optional waiting polls `get_cluster` until ACTIVE/STOPPED, rejecting unexpected
states and stopping at a deadline. Connect/read timeouts are 10/30 seconds;
in-flight calls can extend the polling deadline. A timeout or interruption never
cancels a cloud operation. Exit codes remain 0 success, 1 failure, 2 invalid
settings, and 130 interruption.

## Permissions and operational scope

The caller needs OCI instance visibility and Workbench list/read/lifecycle
permissions for the selected scopes. Name lookup additionally needs IAM
compartment discovery. Exact IAM policies and Workbench roles require validation
in the target tenancy. No policy changes, resource creation, deletion or
implicit rollback are performed. Starting can incur costs and stopping can
interrupt workloads. Recovery consists of a status check and an appropriate
explicitly authorized action.

macOS is the local test platform. Remote use requires a Python-capable OCI AI DP
runtime, packages, API-key configuration and API connectivity. Conda is not
required remotely. Remote compatibility is pending validation. The instance-level
default cluster is outside scope.

## Acceptance criteria

1. Use installed Oracle clients and typed models for all Workbench operations.
2. Preserve settings precedence, exact-name discovery, complete pagination,
   duplicate rejection and compartment boundaries.
3. Preserve status, dry-run, no-op, in-progress, failure and timeout behavior.
4. Verify actual SDK endpoint selection, request serialization, ETags, retry
   tokens and response deserialization using fake HTTP responses for Spark and AI
   Compute, without credentials or live requests.
5. Verify HTTP service errors are not resubmitted and unknown transport outcomes
   are reported without exposing private error content.
6. Pass Black, Pylint and pytest in the project environment. Keep results in this
   specification or development documentation, not README status claims.
7. Separately verify discovery/start/stop on an explicitly authorized live cluster.
   This criterion remains pending; offline tests cannot satisfy it.

## Sources and evidence

Inspected on 2026-09-15:

* [Oracle AI DP SDK repository](https://github.com/oracle-samples/aidataplatform-sdk)
* [Release 4.2.1 and checksums](https://github.com/oracle-samples/aidataplatform-sdk/releases/tag/v4.2.1)
* [ClusterClient source at v4.2.1](https://github.com/oracle-samples/aidataplatform-sdk/blob/v4.2.1/aidp-python-client/src/aidp_python_client/aidataplatform_dp/cluster_client.py)
* [Workbench REST contract](https://docs.oracle.com/en/cloud/paas/ai-data-platform/aiwap/api-cluster.html)

Also inspected the installed wheel metadata, generated client constructors and
methods, and OCI pagination/transport implementation. The wheel constrains OCI
to the 2.165 series; using it with OCI 2.186 would violate its declared dependency.

Verification environment: user-created Conda `codex-4-oci-aidp`, Python 3.11.0 on
macOS, AI DP SDK 4.2.1 and OCI 2.165.1. Black, Pylint and pytest commands are in
`DEVELOPMENT.md`. Tests use real generated clients with replaced HTTP transport
and a fake signer; no local OCI config/keys are read and no live calls are made.
Final checks: 63 pytest tests passed; Black check passed for all five Python
files; Pylint completed at 10.00/10; pip check found no broken requirements.
Installing the root requirements-dev.txt and running CLI help also succeeded.
The configured target's read access and status path are verified. Mutation
permissions and live start/stop behavior remain unverified.
No exact Astra model identifier is available in the session metadata.

## Shared infrastructure extraction (2026-09-15)

Authentication, managed clients, connection settings, compartment and active
instance discovery, banners and sanitized diagnostics now reside in `aidp_common`.
Lifecycle-specific options and operations remain here. Preserve all existing
behavior, especially timestamp compatibility, ETags and no-retry mutations.
See `../../aidp_common/specs/001-shared-infrastructure.md` for the contract and
`../../catalog_tree/specs/001-catalog-tree.md` for final regression evidence.

## Shared workspace-name regression (2026-09-15)

`WORKSPACE_NAME` is now a shared connection setting used by the MCP server and
the cluster lifecycle command. The lifecycle parser must not re-register
`--workspace-name`, because `connection_parser` already supplies that option.
The duplicated registration caused `argparse.ArgumentError` before validation
or any OCI client construction. Removing the local duplicate preserves CLI >
environment > dotenv precedence and the existing mutual-exclusion validation
with `WORKSPACE_KEY`.

Verification: the complete offline test suite, Black, and Pylint must pass;
no OCI API call is required or authorized for this parser-only correction.
