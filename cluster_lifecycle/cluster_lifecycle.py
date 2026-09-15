"""
Author: L. Saetta
Date last modified: 2026-09-15
License: MIT
Description: Discover and start or stop an OCI AI DP Workbench cluster.
"""

import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from contextlib import ExitStack
from uuid import uuid4
from pathlib import Path

# Direct file execution puts the feature folder, not the repository, on sys.path.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Imports follow the path bootstrap required by direct script execution.
# pylint: disable=wrong-import-position
import oci
from aidp_python_client.aidataplatform_dp import ClusterClient, WorkspaceClient, models

from configuration import parse_settings

from aidp_common.connection import (
    AidpError as LifecycleError,
    validate_resource_key,
    load_auth,
    managed_client,
    resolve_compartment,
    list_instances,
)
from aidp_common.output import print_banner, report_unexpected_error


@dataclass(frozen=True)
class Target:
    """Unique cluster identifiers passed to the generated SDK methods."""

    instance_id: str
    workspace_key: str
    cluster_key: str

    def __post_init__(self):
        for value in (self.instance_id, self.workspace_key, self.cluster_key):
            validate_resource_key(value)

    @property
    def sdk_arguments(self):
        """Return the named resource identifiers required by ClusterClient."""
        return {
            "ai_data_platform_id": self.instance_id,
            "workspace_key": self.workspace_key,
            "cluster_key": self.cluster_key,
        }


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
    instances = list_instances(control, compartment_id, instance_id)
    matches = []
    for instance in instances:
        validate_resource_key(instance.id)
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
            validate_resource_key(key)
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


def _execute(args):
    """Execute validated settings, returning 0 on success or 1 on failure."""
    stage = "loading OCI configuration"
    try:
        config, client_options = load_auth(args)
        sdk_options = dict(client_options)
        if args.endpoint:
            sdk_options["service_endpoint"] = args.endpoint
        with ExitStack() as resources:
            stage = "initializing SDK clients"
            identity = managed_client(
                resources, oci.identity.IdentityClient, config, client_options
            )
            control = managed_client(
                resources,
                oci.ai_data_platform.AiDataPlatformClient,
                config,
                client_options,
            )
            workspaces_client = managed_client(
                resources,
                WorkspaceClient,
                config,
                sdk_options,
                preserve_timestamps=True,
            )
            clusters_client = managed_client(
                resources,
                ClusterClient,
                config,
                sdk_options,
                preserve_timestamps=True,
            )
            stage = "resolving the compartment"
            compartment = resolve_compartment(
                identity, config["tenancy"], args.compartment
            )
            stage = "discovering the Workbench cluster"
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
            stage = f"executing cluster {args.action}"
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
        report_unexpected_error(exc, stage)
    return 1


# Each CLI owns its exception boundary and clock for independent execution.
# pylint: disable=duplicate-code
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
    print_banner(operation, datetime.now(timezone.utc))
    try:
        return _execute(args)
    except KeyboardInterrupt:
        print(
            "Interrupted; a submitted cloud operation is not cancelled. Check status.",
            file=sys.stderr,
        )
        return 130
    finally:
        print_banner(
            operation, datetime.now(timezone.utc), elapsed=time.monotonic() - started
        )


if __name__ == "__main__":
    sys.exit(main())
