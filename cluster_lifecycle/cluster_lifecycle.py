"""
Author: L. Saetta
Date last modified: 2026-09-15
License: MIT
Description: Discover and start or stop an OCI AI DP Workbench cluster.
"""

import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from contextlib import ExitStack
from uuid import uuid4

import oci
from aidp_python_client.aidataplatform_dp import ClusterClient, WorkspaceClient, models

from configuration import parse_settings


class LifecycleError(Exception):
    """An actionable discovery, API, or lifecycle failure."""


def _validate_resource_key(value):
    """Reject path separators that OCI 2.165.1 does not escape in path parameters."""
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(character in value for character in ("/", "\\"))
        or value in (".", "..")
    ):
        raise LifecycleError("Resource keys must be nonempty single path segments.")


@dataclass(frozen=True)
class Target:
    """Unique cluster identifiers passed to the generated SDK methods."""

    instance_id: str
    workspace_key: str
    cluster_key: str

    def __post_init__(self):
        for value in (self.instance_id, self.workspace_key, self.cluster_key):
            _validate_resource_key(value)

    @property
    def sdk_arguments(self):
        """Return the named resource identifiers required by ClusterClient."""
        return {
            "ai_data_platform_id": self.instance_id,
            "workspace_key": self.workspace_key,
            "cluster_key": self.cluster_key,
        }


def resolve_compartment(identity, tenancy_id, value):
    """Resolve an OCID or an exact accessible active compartment name.

    Args:
        identity: OCI IdentityClient.
        tenancy_id: Root tenancy OCID from the selected profile.
        value: Compartment name, compartment OCID, or root tenancy OCID.

    Returns:
        The selected compartment OCID.

    Raises:
        LifecycleError: No unique name match is visible.
    """
    if value.startswith(("ocid1.compartment.", "ocid1.tenancy.")):
        return value
    compartments = oci.pagination.list_call_get_all_results(
        identity.list_compartments,
        tenancy_id,
        compartment_id_in_subtree=True,
        access_level="ACCESSIBLE",
        lifecycle_state="ACTIVE",
    ).data
    matches = [item.id for item in compartments if item.name == value]
    if len(matches) != 1:
        raise LifecycleError(
            f"Compartment name has {len(matches)} visible matches; supply its OCID. "
            "For the root compartment, supply the tenancy OCID."
        )
    return matches[0]


def discover(
    control,
    workspaces_client,
    clusters_client,
    compartment_id,
    name,
    *,
    instance_id=None,
    workspace_key=None,
    workspace_name=None,
    cluster_type=None,
):
    """Find exactly one cluster, aborting on incomplete discovery.

    Args:
        control: OCI AiDataPlatformClient.
        workspaces_client: Generated WorkspaceClient.
        clusters_client: Generated ClusterClient.
        compartment_id: Resolved compartment OCID.
        name: Exact, case-sensitive cluster display name.
        instance_id: Optional instance OCID filter.
        workspace_key: Optional workspace key filter.
        workspace_name: Optional exact workspace display name.
        cluster_type: Optional USER or AI_COMPUTE filter.

    Returns:
        A unique Target in the selected scope.

    Raises:
        LifecycleError: Missing or ambiguous target, or invalid instance scope.
    """
    if instance_id:
        instance = control.get_ai_data_platform(instance_id).data
        if (
            instance.compartment_id != compartment_id
            or instance.lifecycle_state != "ACTIVE"
        ):
            raise LifecycleError(
                "Selected instance must be active in the requested compartment."
            )
        instances = [instance]
    else:
        instances = oci.pagination.list_call_get_all_results(
            control.list_ai_data_platforms,
            compartment_id=compartment_id,
            lifecycle_state="ACTIVE",
        ).data
    matches = []
    for instance in instances:
        _validate_resource_key(instance.id)
        workspaces = (
            [models.WorkspaceSummary(key=workspace_key)]
            if workspace_key
            else oci.pagination.list_call_get_all_results(
                workspaces_client.list_workspaces, instance.id
            ).data
        )
        for workspace in workspaces:
            if workspace_name and workspace.display_name != workspace_name:
                continue
            key = workspace.key
            if not isinstance(key, str) or not key:
                raise LifecycleError("Workspace response is missing its key.")
            _validate_resource_key(key)
            filters = {"display_name": name}
            if cluster_type:
                filters["type"] = cluster_type
            clusters = oci.pagination.list_call_get_all_results(
                clusters_client.list_clusters, instance.id, key, **filters
            ).data
            for cluster in clusters:
                if cluster.display_name != name:
                    continue
                if cluster_type and cluster.type != cluster_type:
                    continue
                cluster_key = cluster.key
                if not isinstance(cluster_key, str) or not cluster_key:
                    raise LifecycleError("Cluster response is missing its key.")
                matches.append(Target(instance.id, key, cluster_key))
    if len(matches) != 1:
        raise LifecycleError(
            f"Cluster name has {len(matches)} visible matches. Check the compartment, "
            "region and name; narrow the scope with --instance-id, --workspace-key "
            "or --cluster-type when necessary."
        )
    return matches[0]


def _submit_action(client, target, action, headers):
    """Submit one typed SDK action and preserve uncertain-outcome diagnostics."""
    options = {
        **target.sdk_arguments,
        "retry_strategy": oci.retry.NoneRetryStrategy(),
        "opc_retry_token": str(uuid4()),
    }
    if headers.get("etag"):
        options["if_match"] = headers["etag"]
    try:
        if action == "start":
            result = client.start_cluster(
                start_cluster_details=models.StartClusterDetails(), **options
            )
        else:
            result = client.stop_cluster(
                stop_cluster_details=models.StopClusterDetails(), **options
            )
    except oci.exceptions.RequestException as exc:
        raise LifecycleError(
            "Submission outcome is unknown; check status before retrying."
        ) from exc
    if result.status != 202:
        raise LifecycleError(
            "Unexpected action response; check status before retrying."
        )
    return result.headers


def change_state(
    client,
    target,
    action,
    *,
    dry_run=False,
    wait=False,
    wait_timeout=1200,
    poll_interval=10,
):
    """Inspect, optionally submit, and optionally wait for a lifecycle change.

    Args:
        client: Generated ClusterClient.
        target: Resolved Target.
        action: start, stop, or status.
        dry_run: Inspect without submitting a mutation.
        wait: Poll until the desired state is observed.
        wait_timeout: Maximum polling duration in seconds, excluding an in-flight call.
        poll_interval: Delay between status requests in seconds.

    Raises:
        LifecycleError: Unsafe state, API error, or polling timeout.
    """
    if action not in ("start", "stop", "status"):
        raise LifecycleError("Unsupported lifecycle action.")
    if wait_timeout <= 0 or poll_interval <= 0:
        raise LifecycleError("Polling timeout and interval must be positive.")
    response = client.get_cluster(**target.sdk_arguments)
    state = response.data.state
    print(
        json.dumps(
            {
                "instance_id": target.instance_id,
                "workspace_key": target.workspace_key,
                "cluster_key": target.cluster_key,
                "state": state,
                "action": action,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    if not isinstance(state, str) or not state:
        raise LifecycleError("Cluster response is missing its state.")
    if action == "status":
        return
    desired, origin, transition = (
        ("ACTIVE", "STOPPED", "STARTING")
        if action == "start"
        else ("STOPPED", "ACTIVE", "STOPPING")
    )
    if state == desired:
        print(f"Already {desired}; no action submitted.")
        return
    if state not in (origin, transition):
        raise LifecycleError(
            f"Cannot {action} from state {state}; inspect the cluster first."
        )
    if dry_run:
        plan = (
            f"would submit {action}"
            if state == origin
            else "transition already running"
        )
        print(f"Dry run: {plan}.")
        return
    if state == origin:
        result_headers = _submit_action(client, target, action, response.headers)
        print(f"{action.capitalize()} accepted; completion has not yet been verified.")
        # Only explicitly selected tracing metadata is printed, never response bodies.
        print(
            json.dumps(
                {
                    key: result_headers[key]
                    for key in ("opc-request-id", "aidp-async-operation-key")
                    if key in result_headers
                }
            ),
            flush=True,
        )
    else:
        print(f"Already {transition}; no duplicate action submitted.")
    if not wait:
        return
    deadline = time.monotonic() + wait_timeout
    while time.monotonic() < deadline:
        state = client.get_cluster(**target.sdk_arguments).data.state
        if state == desired:
            print(f"Completed: {desired}.")
            return
        if state not in (origin, transition):
            raise LifecycleError(f"Unexpected cluster state while waiting: {state}.")
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(poll_interval, remaining))
    raise LifecycleError(
        "Wait timed out; the cloud operation is not cancelled. Check status."
    )


def _managed_client(resources, client_type, config, options):
    """Register each SDK session immediately so partial setup failures close it."""
    client = client_type(config, **options)
    client.base_client.session.max_redirects = 0
    resources.callback(client.base_client.session.close)
    return client


def _execute(args):
    """Execute validated settings, returning 0 on success or 1 on failure."""
    try:
        config = oci.config.from_file(
            os.path.expanduser(args.config_file), args.profile
        )
        if args.region:
            config["region"] = args.region
        oci.config.validate_config(config)
        if config.get("security_token_file"):
            raise LifecycleError(
                "This script supports API-key profiles; select an API-key profile."
            )
        signer = oci.signer.Signer(
            tenancy=config["tenancy"],
            user=config["user"],
            fingerprint=config["fingerprint"],
            private_key_file_location=os.path.expanduser(config["key_file"]),
            pass_phrase=config.get("pass_phrase"),
        )
        client_options = {
            "signer": signer,
            "timeout": (10, 30),
            "retry_strategy": oci.retry.NoneRetryStrategy(),
        }
        sdk_options = dict(client_options)
        if args.endpoint:
            sdk_options["service_endpoint"] = args.endpoint
        with ExitStack() as resources:
            identity = _managed_client(
                resources, oci.identity.IdentityClient, config, client_options
            )
            control = _managed_client(
                resources,
                oci.ai_data_platform.AiDataPlatformClient,
                config,
                client_options,
            )
            workspaces_client = _managed_client(
                resources, WorkspaceClient, config, sdk_options
            )
            clusters_client = _managed_client(
                resources, ClusterClient, config, sdk_options
            )
            compartment = resolve_compartment(
                identity, config["tenancy"], args.compartment
            )
            target = discover(
                control,
                workspaces_client,
                clusters_client,
                compartment,
                args.cluster_name,
                instance_id=args.instance_id,
                workspace_key=args.workspace_key,
                workspace_name=args.workspace_name,
                cluster_type=args.cluster_type,
            )
            change_state(
                clusters_client,
                target,
                args.action,
                dry_run=args.dry_run,
                wait=args.wait,
                wait_timeout=args.wait_timeout,
                poll_interval=args.poll_interval,
            )
        return 0
    except LifecycleError as exc:
        print(f"Error: {exc}", file=sys.stderr)
    except oci.exceptions.ServiceError as exc:
        print(
            f"OCI HTTP {exc.status}; check region, visibility, permissions and state. "
            "If an action was submitted, check status before retrying.",
            file=sys.stderr,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # Redact third-party errors at the CLI boundary.
        # Third-party exceptions can contain config values or signed request data.
        print(
            f"Error ({type(exc).__name__}): check OCI SDK version, API-key profile, "
            "key accessibility and network configuration.",
            file=sys.stderr,
        )
    return 1


def _print_banner(operation, timestamp, elapsed=None):
    phase = "START" if elapsed is None else "END"
    lines = [
        f"### OCI AI DP cluster lifecycle | {phase}",
        f"### Operation    : {operation}",
        f"### {phase.title() + ' time':13}: {timestamp.isoformat(timespec='seconds')}",
    ]
    if elapsed is not None:
        lines.append(f"### Elapsed time : {elapsed:.3f} seconds")
    border = "#" * 72
    print("\n".join([border, *lines, border]), flush=True)


def main(argv=None):
    """Run an operation with UTC timestamps and monotonic elapsed timing.

    Args:
        argv: Optional CLI arguments; defaults to process arguments.

    Returns:
        Exit code 0 for success, 1 for failure, or 130 for interruption.
        Argument parsing errors exit with code 2 before execution begins.
    """
    args = parse_settings(argv)
    operation = args.action.upper() + (" (DRY RUN)" if args.dry_run else "")
    started = time.monotonic()
    _print_banner(operation, datetime.now(timezone.utc))
    try:
        return _execute(args)
    except KeyboardInterrupt:
        print(
            "Interrupted; a submitted cloud operation is not cancelled. Check status.",
            file=sys.stderr,
        )
        return 130
    finally:
        _print_banner(
            operation, datetime.now(timezone.utc), elapsed=time.monotonic() - started
        )


if __name__ == "__main__":
    sys.exit(main())
