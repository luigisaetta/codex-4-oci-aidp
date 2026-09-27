"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: AI DP target discovery and process-lifetime target cache.
"""

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from threading import Lock

import oci
from aidp_python_client.aidataplatform_dp import (
    CatalogClient,
    ClusterClient,
    AgentClient,
    NotebookClient,
    SchemaClient,
    VolumeClient,
    WorkflowClient,
    WorkspaceClient,
)

from aidp_common.connection import (
    AidpError,
    list_instances,
    load_auth,
    managed_client,
    resolve_compartment,
    validate_resource_key,
)
from aidp_mcp.lookups import find_workspace

TARGET_CACHE_LOCK = Lock()
_TARGET_CACHE = {}


@dataclass(frozen=True)
class ResolvedTarget:
    """Cached identifiers for one configured AI DP target."""

    instance_id: str
    workspace_key: str | None = None


def clear_target_cache():
    """Remove all cached AI DP target identifiers.

    This is primarily for deterministic offline tests. Production invalidation
    removes only the target whose cached use received an HTTP 404 response.
    """
    with TARGET_CACHE_LOCK:
        _TARGET_CACHE.clear()


def _target_cache_key(settings, tenancy_id):
    """Build a stable cache key from target-selection inputs and tenancy."""
    fields = (
        "config_file",
        "profile",
        "region",
        "compartment",
        "instance_id",
        "workspace_name",
        "endpoint",
    )
    return tuple(getattr(settings, field, None) for field in fields) + (tenancy_id,)


def _clear_target_cache_entry(key):
    """Remove one target only when it is still present in the process cache."""
    with TARGET_CACHE_LOCK:
        _TARGET_CACHE.pop(key, None)


def _discover_instances(identity, control, config, settings):
    """Resolve the configured instance without tenancy-wide listing when possible.

    Args:
        identity: Short-lived OCI Identity client used only for name validation.
        control: Short-lived AI DP control-plane client.
        config: Validated OCI configuration containing the tenancy OCID.
        settings: Validated MCP connection settings.

    Returns:
        list: The active selected instance, or the active instances selected by
        the existing compartment-based discovery path.

    Raises:
        AidpError: The explicit instance is inactive or outside the named
        compartment, or normal discovery cannot select an instance.
    """
    compartment_is_ocid = settings.compartment.startswith(
        ("ocid1.compartment.", "ocid1.tenancy.")
    )
    if settings.instance_id and not compartment_is_ocid:
        instance = control.get_ai_data_platform(settings.instance_id).data
        compartment = identity.get_compartment(instance.compartment_id).data
        if (
            getattr(instance, "lifecycle_state", None) != "ACTIVE"
            or getattr(compartment, "lifecycle_state", None) != "ACTIVE"
            or getattr(compartment, "name", None) != settings.compartment
        ):
            raise AidpError(
                "Selected instance must be active in the requested compartment."
            )
        return [instance]
    compartment_id = resolve_compartment(
        identity, config["tenancy"], settings.compartment
    )
    return list_instances(control, compartment_id, settings.instance_id)


def _resolve_target(
    settings, config, options, workbench_options, resources, *, need_workspace
):
    """Return one cached or newly discovered AI DP target.

    The process-wide lock intentionally covers discovery. This prevents two
    FastMCP worker threads from repeating a slow first resolution for the same
    target; ordinary tool work executes after the lock is released.

    Args:
        settings: Validated MCP connection settings.
        config: Validated OCI configuration.
        options: OCI signer, timeout, and retry options.
        workbench_options: Workbench client options, including an endpoint
        override when configured.
        resources: ExitStack that owns clients created during this request.
        need_workspace: Whether the caller requires a workspace key.

    Returns:
        tuple[ResolvedTarget, tuple, bool]: Target, its cache key, and whether
        a complete target was served from the cache.

    Raises:
        AidpError: Discovery cannot select exactly one active instance or
        workspace.
    """
    key = _target_cache_key(settings, config["tenancy"])
    with TARGET_CACHE_LOCK:
        target = _TARGET_CACHE.get(key)
        if target and (not need_workspace or target.workspace_key is not None):
            return target, key, True

        if target is None:
            identity = managed_client(
                resources, oci.identity.IdentityClient, config, options
            )
            control = managed_client(
                resources, oci.ai_data_platform.AiDataPlatformClient, config, options
            )
            instances = _discover_instances(identity, control, config, settings)
            if len(instances) != 1:
                raise AidpError("Exactly one active AI DP instance must be selected.")
            instance_id = instances[0].id
            validate_resource_key(instance_id)
            target = ResolvedTarget(instance_id)

        if need_workspace and target.workspace_key is None:
            workspaces = managed_client(
                resources,
                WorkspaceClient,
                config,
                workbench_options,
                preserve_timestamps=True,
            )
            workspace_key = find_workspace(
                workspaces, target.instance_id, settings.workspace_name
            )
            target = ResolvedTarget(target.instance_id, workspace_key)

        _TARGET_CACHE[key] = target
        return target, key, False


@contextmanager
def workspace_clients(settings):
    """Create request-scoped workspace clients for validated settings."""
    config, options = load_auth(settings)
    workbench_options = dict(options)
    if settings.endpoint:
        workbench_options["service_endpoint"] = settings.endpoint
    resources = ExitStack()
    cache_key = None
    cache_hit = False
    try:
        target, cache_key, cache_hit = _resolve_target(
            settings,
            config,
            options,
            workbench_options,
            resources,
            need_workspace=True,
        )
        # AI DP can return numeric timestamps in response models. These tools do
        # not interpret timestamps, so preserve their service representation and
        # prevent OCI SDK datetime deserialization from rejecting discovery.
        clusters = managed_client(
            resources,
            ClusterClient,
            config,
            workbench_options,
            preserve_timestamps=True,
        )
        notebooks = managed_client(
            resources,
            NotebookClient,
            config,
            workbench_options,
            preserve_timestamps=True,
        )
        workflows = managed_client(
            resources,
            WorkflowClient,
            config,
            workbench_options,
            preserve_timestamps=True,
        )
        yield (
            target.instance_id,
            target.workspace_key,
            clusters,
            notebooks,
            workflows,
        )
    except oci.exceptions.ServiceError as exc:
        if cache_hit and exc.status == 404:
            _clear_target_cache_entry(cache_key)
        raise
    finally:
        resources.close()


@contextmanager
def catalog_clients(settings):
    """Create short-lived clients for AI DP catalog discovery.

    Catalog resources are scoped to the AI DP instance, rather than a
    workspace. Keeping this separate avoids adding workspace objects to
    read-only volume discovery requests.
    """
    config, options = load_auth(settings)
    workbench_options = dict(options)
    if settings.endpoint:
        workbench_options["service_endpoint"] = settings.endpoint
    resources = ExitStack()
    cache_key = None
    cache_hit = False
    try:
        target, cache_key, cache_hit = _resolve_target(
            settings,
            config,
            options,
            workbench_options,
            resources,
            need_workspace=False,
        )
        catalogs, schemas, volumes = (
            managed_client(
                resources,
                client_class,
                config,
                workbench_options,
                preserve_timestamps=True,
            )
            for client_class in (CatalogClient, SchemaClient, VolumeClient)
        )
        yield target.instance_id, catalogs, schemas, volumes
    except oci.exceptions.ServiceError as exc:
        if cache_hit and exc.status == 404:
            _clear_target_cache_entry(cache_key)
        raise
    finally:
        resources.close()


@contextmanager
def agent_clients(settings):
    """Create request-scoped clients for configured-workspace agent reads.

    Agent responses can contain numeric timestamps, so this context preserves
    their service representation just as the other Workbench client contexts do.
    """
    config, options = load_auth(settings)
    workbench_options = dict(options)
    if settings.endpoint:
        workbench_options["service_endpoint"] = settings.endpoint
    resources = ExitStack()
    cache_key = None
    cache_hit = False
    try:
        target, cache_key, cache_hit = _resolve_target(
            settings,
            config,
            options,
            workbench_options,
            resources,
            need_workspace=True,
        )
        agents = managed_client(
            resources,
            AgentClient,
            config,
            workbench_options,
            preserve_timestamps=True,
        )
        yield target.instance_id, target.workspace_key, agents
    except oci.exceptions.ServiceError as exc:
        if cache_hit and exc.status == 404:
            _clear_target_cache_entry(cache_key)
        raise
    finally:
        resources.close()
