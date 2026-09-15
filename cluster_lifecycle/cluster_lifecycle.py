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
from urllib.parse import quote

import oci
import requests

from configuration import endpoint_origin, parse_settings

API_VERSION = "20260430"


class LifecycleError(Exception):
    """An actionable discovery, API, or lifecycle failure."""


def _segment(value):
    return quote(value, safe="")


@dataclass(frozen=True)
class Target:
    """Unique cluster address resolved within the requested compartment."""

    instance_id: str
    workspace_key: str
    cluster_key: str

    @property
    def path(self):
        """Return the REST resource path with individually encoded identifiers."""
        return (
            f"/{API_VERSION}/aiDataPlatforms/{_segment(self.instance_id)}"
            f"/workspaces/{_segment(self.workspace_key)}"
            f"/clusters/{_segment(self.cluster_key)}"
        )


class WorkbenchClient:
    """Small signed REST adapter; no redirects or automatic mutation retries.

    Args:
        endpoint: Trusted Workbench HTTPS service origin.
        signer: OCI API-key request signer.
        session: Optional Requests-compatible session for offline testing.
    """

    def __init__(self, endpoint, signer, session=None):
        self.endpoint = endpoint_origin(endpoint)
        self.session = session if session is not None else requests.Session()
        self.session.auth = signer

    def request(self, method, path, *, params=None, etag=None):
        """Send one signed request and return its JSON object and headers.

        Args:
            method: GET or POST.
            path: Encoded API resource path.
            params: Optional query parameters.
            etag: Optional concurrency guard for a mutation.

        Returns:
            A pair containing the decoded response object and response headers.

        Raises:
            LifecycleError: Transport, HTTP, or response-schema failure.
        """
        headers = {"accept": "application/json"}
        if etag:
            headers["if-match"] = etag
        options = {"json": {}} if method == "POST" else {}
        try:
            response = self.session.request(
                method,
                self.endpoint + path,
                params=params,
                headers=headers,
                timeout=(10, 30),
                allow_redirects=False,
                **options,
            )
        except requests.RequestException as exc:
            advice = (
                " Submission outcome is unknown; check status before retrying."
                if method == "POST"
                else " Check connectivity and the Workbench endpoint."
            )
            raise LifecycleError("Workbench request failed." + advice) from exc
        expected_status = 202 if method == "POST" else 200
        if response.status_code != expected_status:
            raise LifecycleError(
                f"Workbench HTTP {response.status_code}; expected {expected_status}. "
                "Check authentication/permissions (401/403), endpoint and resource "
                "visibility (404), current state (409/412), or throttling (429). "
                "For a submitted action, check status before retrying."
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise LifecycleError(
                "Workbench returned invalid JSON; check endpoint/API compatibility. "
                "If an action was submitted, check status before retrying."
            ) from exc
        if not isinstance(data, dict):
            raise LifecycleError("Workbench response must be a JSON object.")
        return data, response.headers

    def items(self, path, **filters):
        """Yield every collection item, rejecting malformed or looping pages.

        Args:
            path: Encoded collection path.
            **filters: Documented query filters.

        Yields:
            Resource dictionaries from every response page.

        Raises:
            LifecycleError: Invalid collection or repeated pagination token.
        """
        params = {**filters, "limit": 100}
        seen = set()
        while True:
            data, headers = self.request("GET", path, params=params)
            items = data.get("items")
            if not isinstance(items, list) or any(
                not isinstance(x, dict) for x in items
            ):
                raise LifecycleError("Workbench collection is missing valid items.")
            yield from items
            token = headers.get("opc-next-page")
            if not token:
                return
            if token in seen:
                raise LifecycleError("Workbench repeated a pagination token.")
            seen.add(token)
            params = {**params, "page": token}


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
    workbench,
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
        workbench: WorkbenchClient.
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
        base = f"/{API_VERSION}/aiDataPlatforms/{_segment(instance.id)}/workspaces"
        workspaces = (
            [{"key": workspace_key}] if workspace_key else workbench.items(base)
        )
        for workspace in workspaces:
            if workspace_name and workspace.get("displayName") != workspace_name:
                continue
            key = workspace.get("key")
            if not isinstance(key, str) or not key:
                raise LifecycleError("Workspace response is missing its key.")
            filters = {"displayName": name}
            if cluster_type:
                filters["type"] = cluster_type
            for cluster in workbench.items(
                f"{base}/{_segment(key)}/clusters", **filters
            ):
                if cluster.get("displayName") != name:
                    continue
                if cluster_type and cluster.get("type") != cluster_type:
                    continue
                cluster_key = cluster.get("key")
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
        client: WorkbenchClient.
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
    data, headers = client.request("GET", target.path)
    state = data.get("state")
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
        _, result_headers = client.request(
            "POST",
            target.path + f"/actions/{action}",
            etag=headers.get("etag"),
        )
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
        data, _ = client.request("GET", target.path)
        state = data.get("state")
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


def main(argv=None):
    """Run the command and return 0 for success or 1 for operational failure.

    Args:
        argv: Optional CLI argument list; defaults to process arguments.

    Returns:
        Process exit code. Argument parsing errors exit with code 2.
    """
    args = parse_settings(argv)
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
        identity = oci.identity.IdentityClient(config, **client_options)
        control = oci.ai_data_platform.AiDataPlatformClient(config, **client_options)
        compartment = resolve_compartment(identity, config["tenancy"], args.compartment)
        with requests.Session() as session:
            client = WorkbenchClient(args.endpoint, signer, session)
            target = discover(
                control,
                client,
                compartment,
                args.cluster_name,
                instance_id=args.instance_id,
                workspace_key=args.workspace_key,
                workspace_name=args.workspace_name,
                cluster_type=args.cluster_type,
            )
            change_state(
                client,
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
            f"OCI HTTP {exc.status}; check region, visibility and IAM permissions.",
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


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(
            "Interrupted; a submitted cloud operation is not cancelled. Check status.",
            file=sys.stderr,
        )
        sys.exit(130)
