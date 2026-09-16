"""
Author: L. Saetta
Date last modified: 2026-09-16
License: MIT
Description: Validated AI DP notebook upload and single-task workflow operations.
"""

# This deliberately keeps the MCP service's cohesive validation and response
# boundary in one module; its supported operation set exceeds Pylint's default.
# pylint: disable=too-many-lines

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import time
from urllib.parse import quote
from uuid import uuid4

import oci
from aidp_python_client.aidataplatform_dp import (
    CatalogClient,
    ClusterClient,
    NotebookClient,
    SchemaClient,
    VolumeClient,
    WorkflowClient,
    WorkspaceClient,
    models,
)

from aidp_common.connection import (
    AidpError,
    list_instances,
    load_auth,
    managed_client,
    resolve_compartment,
    validate_resource_key,
)
from aidp_common.settings import connection_parser, validate_connection

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TERMINAL_JOB_STATES = {"SUCCESS", "FAILED", "ERROR", "CANCELED", "TIMED_OUT"}
MAX_JOB_RUN_OUTPUT_CHARACTERS = 12000
MAX_JOB_RUN_LIST_RESULTS = 1000
MAX_NOTEBOOK_LIST_RESULTS = 1000
MAX_NOTEBOOK_JOB_SEARCH_RESULTS = 1000
MAX_CATALOG_VOLUME_RESULTS = 1000
MAX_VOLUME_FILE_RESULTS = 1000


@dataclass(frozen=True)
class Target:
    """Resolved AI DP instance, workspace, and cluster identifiers."""

    instance_id: str
    workspace_key: str
    cluster_key: str
    cluster_name: str


def load_connection_settings():
    """Load and validate the shared project connection settings.

    Returns:
        argparse.Namespace: Validated shared connection settings.

    Raises:
        SystemExit: The project configuration is incomplete or invalid.
    """
    parser, _, _ = connection_parser([], "Run AI DP notebook workflow MCP tools.")
    args = parser.parse_args([])
    validate_connection(args, parser)
    if not args.workspace_name:
        parser.error("Set WORKSPACE_NAME before using AI DP MCP tools.")
    return args


def validate_local_notebook(local_path):
    """Read a local notebook safely and return its parsed JSON and digest.

    Args:
        local_path: Repository-relative or absolute notebook path.

    Returns:
        tuple[Path, dict, str]: Canonical path, parsed notebook JSON, SHA-256.

    Raises:
        AidpError: The path is unsafe, absent, not a notebook, or invalid JSON.
    """
    candidate = Path(local_path).expanduser().resolve()
    try:
        candidate.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise AidpError("Local notebook path must be inside the repository.") from exc
    if candidate.suffix != ".ipynb" or not candidate.is_file():
        raise AidpError("Local notebook must be an existing .ipynb file.")
    try:
        payload = candidate.read_bytes()
        content = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AidpError("Local notebook must contain valid UTF-8 JSON.") from exc
    if not isinstance(content, dict) or not content:
        raise AidpError("Local notebook JSON must be a nonempty object.")
    return candidate, content, hashlib.sha256(payload).hexdigest()


def validate_workspace_path(workspace_path):
    """Validate an SDK workspace-relative notebook path without traversal.

    Args:
        workspace_path: POSIX path relative to the AI DP workspace root.

    Returns:
        str: Normalized workspace path.

    Raises:
        AidpError: The path is not a relative notebook path.
    """
    path = PurePosixPath(workspace_path)
    if (
        path.is_absolute()
        or path.suffix != ".ipynb"
        or ".." in path.parts
        or path == PurePosixPath(".")
    ):
        raise AidpError("Workspace path must be a relative .ipynb path.")
    return str(path)


def workspace_content_path(workspace_path):
    """Return the notebook service's absolute path for a relative input.

    Args:
        workspace_path: Valid path relative to the workspace root.

    Returns:
        str: Absolute notebook service path beneath `/Workspace`.
    """
    return f"/Workspace/{workspace_path}"


def validate_workspace_directory(path):
    """Validate an absolute AI DP workspace directory path.

    Args:
        path: Absolute directory path rooted at ``/Workspace``.

    Returns:
        str: Normalized absolute workspace directory path.

    Raises:
        AidpError: The path is not a safe workspace directory.
    """
    if not isinstance(path, str) or not path.strip():
        raise AidpError("Workspace directory path must be a nonempty string.")
    candidate = PurePosixPath(path)
    if not candidate.is_absolute() or candidate.parts[1:2] != ("Workspace",):
        raise AidpError("Workspace directory path must be rooted at /Workspace.")
    if any(part in (".", "..") for part in candidate.parts):
        raise AidpError("Workspace directory path must not contain traversal.")
    return str(candidate)


def validate_workspace_notebook_path(path):
    """Validate an absolute notebook path used for workspace discovery.

    Args:
        path: Absolute notebook path rooted at ``/Workspace``.

    Returns:
        str: Normalized absolute notebook path.

    Raises:
        AidpError: The path is not a safe workspace notebook path.
    """
    candidate = PurePosixPath(validate_workspace_directory(path))
    if candidate.suffix != ".ipynb":
        raise AidpError("Workspace notebook path must end in .ipynb.")
    return str(candidate)


def validate_resource_name(value, label):
    """Validate an exact AI DP display-name selector.

    Args:
        value: Candidate catalog, schema, or volume display name.
        label: Human-readable resource type for the error message.

    Returns:
        str: The unchanged, nonempty display name.

    Raises:
        AidpError: The selector is not a supported display name.
    """
    if not isinstance(value, str) or not value.strip() or len(value) > 255:
        raise AidpError(f"{label} name must be a nonempty string up to 255 characters.")
    return value


def validate_volume_path(path):
    """Validate one absolute, traversal-free path within a volume.

    Args:
        path: Absolute POSIX path rooted at the volume root.

    Returns:
        str: Normalized POSIX path.

    Raises:
        AidpError: The path is empty, relative, or contains traversal.
    """
    if not isinstance(path, str) or not path.strip():
        raise AidpError("Volume path must be a nonempty absolute POSIX path.")
    candidate = PurePosixPath(path)
    if not candidate.is_absolute() or any(
        part in (".", "..") for part in candidate.parts
    ):
        raise AidpError("Volume path must be absolute and must not contain traversal.")
    return str(candidate)


def _normalized_task_notebook_path(path):
    """Normalize a remote notebook-task path for a safe equality comparison."""
    if not isinstance(path, str) or not path:
        return None
    candidate = PurePosixPath(path)
    try:
        if candidate.is_absolute():
            return validate_workspace_notebook_path(path)
        return workspace_content_path(validate_workspace_path(path))
    except AidpError:
        return None


def encoded_content_path(content_path):
    """Encode an absolute notebook path for the SDK URL path parameter.

    Args:
        content_path: Absolute path returned or accepted by the notebook service.

    Returns:
        str: URL-path-safe encoded content path.
    """
    return quote(content_path, safe="")


def notebook_content_request(
    notebooks, *, instance_id, workspace_key, method, content_path, body=None
):
    """Call the documented notebook-content endpoint without double encoding.

    The generated Python client turns `%2F` into `%252F` when an encoded path
    is supplied as a path parameter. The notebook service requires encoded
    slashes inside its final URL segment.

    Args:
        notebooks: Generated notebook client with the configured signer.
        instance_id: Selected AI DP instance OCID.
        workspace_key: Selected workspace key.
        method: HTTP method accepted by the notebook contents endpoint.
        content_path: Absolute notebook service path below `/Workspace`.
        body: Optional generated SDK request model.

    Returns:
        oci.response.Response: Notebook content service response.
    """
    return notebooks.base_client.call_api(
        resource_path=(
            "/aiDataPlatforms/{aiDataPlatformId}/workspaces/{workspaceKey}"
            f"/notebook/api/contents/{encoded_content_path(content_path)}"
        ),
        method=method,
        path_params={
            "aiDataPlatformId": instance_id,
            "workspaceKey": workspace_key,
        },
        header_params={
            "accept": "application/json",
            "content-type": "application/json",
        },
        body=body,
        response_type="Content",
    )


def workspace_objects_request(
    notebooks, *, instance_id, workspace_key, path, limit, page=None
):
    """List notebook workspace-object summaries through the documented API.

    Args:
        notebooks: Generated notebook client with configured signer and endpoint.
        instance_id: Selected AI DP instance OCID.
        workspace_key: Selected workspace key.
        path: Absolute workspace directory path.
        limit: Maximum summaries to return in this page.
        page: Optional OCI page token from the preceding response.

    Returns:
        oci.response.Response: A page of ``WorkspaceObjectCollection`` data.
    """
    query_params = {"path": path, "type": "NOTEBOOK", "limit": limit}
    if page:
        query_params["page"] = page
    return notebooks.base_client.call_api(
        resource_path=(
            "/aiDataPlatforms/{aiDataPlatformId}/workspaces/{workspaceKey}/objects"
        ),
        method="GET",
        path_params={
            "aiDataPlatformId": instance_id,
            "workspaceKey": workspace_key,
        },
        query_params=query_params,
        header_params={"accept": "application/json"},
        response_type="WorkspaceObjectCollection",
    )


def is_missing_content_error(error):
    """Identify an absent-notebook response from the AI DP content service.

    Args:
        error: OCI service error from a notebook-content GET request.

    Returns:
        bool: Whether the response is the observed absent-content form.
    """
    return error.status == 404 or (
        error.status == 500
        and getattr(error, "code", None) == "InternalError"
        and "getting notebook content" in str(getattr(error, "message", "")).lower()
    )


def is_existing_folder_error(error):
    """Identify AI DP's conflict response for a pre-existing workspace folder.

    Args:
        error: OCI service error from the workspace objects API.

    Returns:
        bool: Whether the service reports that the requested directory exists.
    """
    return (
        error.status == 409
        and getattr(error, "code", None) == "Conflict"
        and "directory already exists" in str(getattr(error, "message", "")).lower()
    )


def create_workspace_folder(notebooks, instance_id, workspace_key, folder_path):
    """Create or retain one workspace folder through the documented objects API.

    Args:
        notebooks: Generated notebook client with the configured endpoint and
            signer.
        instance_id: Selected AI DP instance OCID.
        workspace_key: Selected workspace key.
        folder_path: Absolute folder path below `/Workspace`.

    Raises:
        AidpError: The service does not accept the folder creation request.
    """
    try:
        response = notebooks.base_client.call_api(
            resource_path=(
                "/aiDataPlatforms/{aiDataPlatformId}/workspaces/{workspaceKey}/objects"
            ),
            method="POST",
            path_params={
                "aiDataPlatformId": instance_id,
                "workspaceKey": workspace_key,
            },
            header_params={
                "accept": "*/*",
                "content-type": "application/octet-stream",
                "path": folder_path,
                "type": "FOLDER",
                "is-overwrite": "true",
            },
            body=b"",
            response_type=None,
        )
    except oci.exceptions.ServiceError as exc:
        if is_existing_folder_error(exc):
            return
        raise
    if response.status not in (200, 201):
        raise AidpError(f"Workspace folder creation returned HTTP {response.status}.")


def _resource_key(resource, label):
    value = getattr(resource, "key", None)
    if not isinstance(value, str) or not value:
        raise AidpError(f"{label} response is missing its key.")
    validate_resource_key(value)
    return value


def find_workspace(workspaces, instance_id, workspace_name):
    """Resolve one exact workspace name within an AI DP instance.

    Args:
        workspaces: Generated WorkspaceClient.
        instance_id: AI DP instance OCID.
        workspace_name: Exact workspace display name.

    Returns:
        str: Workspace key.

    Raises:
        AidpError: The workspace is absent or ambiguous.
    """
    matches = [
        item
        for item in oci.pagination.list_call_get_all_results(
            workspaces.list_workspaces, instance_id, display_name=workspace_name
        ).data
        if getattr(item, "display_name", None) == workspace_name
    ]
    if len(matches) != 1:
        raise AidpError(f"Workspace name has {len(matches)} visible matches.")
    return _resource_key(matches[0], "Workspace")


def find_cluster(clusters, instance_id, workspace_key, cluster_name):
    """Resolve one exact active cluster in a workspace.

    Args:
        clusters: Generated ClusterClient.
        instance_id: AI DP instance OCID.
        workspace_key: Workspace key.
        cluster_name: Exact cluster display name.

    Returns:
        Target: Resolved cluster target.

    Raises:
        AidpError: The cluster is absent, ambiguous, or not active.
    """
    matches = [
        item
        for item in oci.pagination.list_call_get_all_results(
            clusters.list_clusters,
            instance_id,
            workspace_key,
            display_name=cluster_name,
        ).data
        if getattr(item, "display_name", None) == cluster_name
    ]
    if len(matches) != 1:
        raise AidpError(f"Cluster name has {len(matches)} visible matches.")
    key = _resource_key(matches[0], "Cluster")
    cluster = clusters.get_cluster(instance_id, workspace_key, key).data
    if getattr(cluster, "state", None) != "ACTIVE":
        raise AidpError("Selected cluster must be ACTIVE before a job run.")
    return Target(instance_id, workspace_key, key, cluster_name)


def find_cluster_status(clusters, instance_id, workspace_key, cluster_name):
    """Resolve one exact cluster and return its current detailed SDK model.

    Args:
        clusters: Generated ClusterClient.
        instance_id: Selected AI DP instance OCID.
        workspace_key: Selected workspace key.
        cluster_name: Exact cluster display name.

    Returns:
        object: The detailed cluster model returned by AI DP.

    Raises:
        AidpError: The cluster is absent, ambiguous, or lacks a resource key.
    """
    return find_cluster_details(clusters, instance_id, workspace_key, cluster_name).data


def find_cluster_details(clusters, instance_id, workspace_key, cluster_name):
    """Resolve one exact cluster and return its detailed SDK response.

    This preserves the ETag required to protect a lifecycle mutation from a
    concurrent cluster update.

    Args:
        clusters: Generated ClusterClient.
        instance_id: Selected AI DP instance OCID.
        workspace_key: Selected workspace key.
        cluster_name: Exact cluster display name.

    Returns:
        oci.response.Response: Detailed cluster response, including headers.

    Raises:
        AidpError: The cluster is absent, ambiguous, or lacks a resource key.
    """
    matches = [
        item
        for item in oci.pagination.list_call_get_all_results(
            clusters.list_clusters,
            instance_id,
            workspace_key,
            display_name=cluster_name,
        ).data
        if getattr(item, "display_name", None) == cluster_name
    ]
    if len(matches) != 1:
        raise AidpError(f"Expected exactly one cluster named {cluster_name!r}.")
    key = _resource_key(matches[0], "Cluster")
    return clusters.get_cluster(instance_id, workspace_key, key)


def _find_job(workflows, instance_id, workspace_key, job_name):
    matches = [
        item
        for item in oci.pagination.list_call_get_all_results(
            workflows.list_jobs, instance_id, workspace_key, display_name=job_name
        ).data
        if getattr(item, "name", getattr(item, "display_name", None)) == job_name
    ]
    if len(matches) > 1:
        raise AidpError(f"Job name has {len(matches)} visible matches.")
    if not matches:
        return None
    return workflows.get_job(
        instance_id, workspace_key, _resource_key(matches[0], "Job")
    )


def _managed_task(notebook_path, cluster_key):
    cluster = models.JobCluster(cluster_key=cluster_key)
    return models.NotebookTask(
        task_key="notebook",
        notebook_path=notebook_path,
        source=models.NotebookTask.SOURCE_WORKSPACE,
        cluster=cluster,
        depends_on=[],
        run_if="ALL_SUCCESS",
        parameters=[],
        max_retries=0,
        is_retry_on_timeout=False,
    )


def _is_supported_job(job):
    tasks = getattr(job, "tasks", None)
    if not isinstance(tasks, list) or len(tasks) != 1:
        return False
    task = tasks[0]
    return (
        getattr(task, "type", None) == "NOTEBOOK_TASK"
        and getattr(task, "source", None) == "WORKSPACE"
        and getattr(task, "task_key", None) == "notebook"
    )


def _matching_notebook_tasks(job, notebook_path):
    """Return sanitized workspace notebook tasks that use one notebook path."""
    matches = []
    for task in getattr(job, "tasks", None) or []:
        if (
            getattr(task, "type", None) != "NOTEBOOK_TASK"
            or getattr(task, "source", None) != "WORKSPACE"
            or _normalized_task_notebook_path(getattr(task, "notebook_path", None))
            != notebook_path
        ):
            continue
        cluster_key = getattr(getattr(task, "cluster", None), "cluster_key", None)
        matches.append(
            {
                "task_key": getattr(task, "task_key", None),
                "cluster_key": cluster_key if isinstance(cluster_key, str) else None,
            }
        )
    return matches


class AidpWorkflowService:
    """Perform scoped notebook and workflow operations through typed SDK clients."""

    def __init__(self, settings=None):
        """Initialize the service without creating cloud clients.

        Args:
            settings: Optional validated shared settings for tests or execution.
        """
        self.settings = settings or load_connection_settings()

    @contextmanager
    def _clients(self):
        config, options = load_auth(self.settings)
        workbench_options = dict(options)
        if self.settings.endpoint:
            workbench_options["service_endpoint"] = self.settings.endpoint
        resources = ExitStack()
        identity = managed_client(
            resources, oci.identity.IdentityClient, config, options
        )
        control = managed_client(
            resources, oci.ai_data_platform.AiDataPlatformClient, config, options
        )
        # AI DP can return numeric timestamps in response models. These tools do
        # not interpret timestamps, so preserve their service representation and
        # prevent OCI SDK datetime deserialization from rejecting discovery.
        workspaces = managed_client(
            resources,
            WorkspaceClient,
            config,
            workbench_options,
            preserve_timestamps=True,
        )
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
        compartment = resolve_compartment(
            identity, config["tenancy"], self.settings.compartment
        )
        instances = list_instances(control, compartment, self.settings.instance_id)
        try:
            if len(instances) != 1:
                raise AidpError("Exactly one active AI DP instance must be selected.")
            instance_id = instances[0].id
            validate_resource_key(instance_id)
            workspace_key = find_workspace(
                workspaces, instance_id, self.settings.workspace_name
            )
            yield instance_id, workspace_key, clusters, notebooks, workflows
        finally:
            resources.close()

    @contextmanager
    def _catalog_clients(self):
        """Create short-lived clients for AI DP catalog discovery.

        Catalog resources are scoped to the AI DP instance, rather than a
        workspace. Keeping this separate avoids adding workspace objects to
        read-only volume discovery requests.
        """
        config, options = load_auth(self.settings)
        workbench_options = dict(options)
        if self.settings.endpoint:
            workbench_options["service_endpoint"] = self.settings.endpoint
        resources = ExitStack()
        identity = managed_client(
            resources, oci.identity.IdentityClient, config, options
        )
        control = managed_client(
            resources, oci.ai_data_platform.AiDataPlatformClient, config, options
        )
        catalogs = managed_client(
            resources,
            CatalogClient,
            config,
            workbench_options,
            preserve_timestamps=True,
        )
        schemas = managed_client(
            resources,
            SchemaClient,
            config,
            workbench_options,
            preserve_timestamps=True,
        )
        volumes = managed_client(
            resources,
            VolumeClient,
            config,
            workbench_options,
            preserve_timestamps=True,
        )
        compartment = resolve_compartment(
            identity, config["tenancy"], self.settings.compartment
        )
        instances = list_instances(control, compartment, self.settings.instance_id)
        try:
            if len(instances) != 1:
                raise AidpError("Exactly one active AI DP instance must be selected.")
            instance_id = instances[0].id
            validate_resource_key(instance_id)
            yield instance_id, catalogs, schemas, volumes
        finally:
            resources.close()

    def list_catalog_volumes(self, catalog_name, external_only=True, max_results=100):
        """List visible volumes below one exact catalog name.

        Args:
            catalog_name: Exact, case-sensitive catalog display name.
            external_only: When true, return only external Object Storage volumes.
            max_results: Maximum returned volumes across all schemas.

        Returns:
            dict: Sanitized schema-to-volume hierarchy and truncation state.

        Raises:
            AidpError: The selection is invalid, ambiguous, or inaccessible.
        """
        catalog_name = validate_resource_name(catalog_name, "Catalog")
        if not isinstance(external_only, bool):
            raise AidpError("external_only must be a boolean.")
        _validate_result_limit(max_results, MAX_CATALOG_VOLUME_RESULTS)
        schema_nodes = []
        matched_count = 0
        is_truncated = False
        with self._catalog_clients() as clients:
            instance_id, catalogs, schemas, volumes = clients
            catalog = _find_exact_catalog(catalogs, instance_id, catalog_name)
            schema_items = oci.pagination.list_call_get_all_results(
                schemas.list_schemas, instance_id, _resource_key(catalog, "Catalog")
            ).data
            for schema in _sorted_named_resources(schema_items, "Schema"):
                volume_items = oci.pagination.list_call_get_all_results(
                    volumes.list_volumes,
                    instance_id,
                    _resource_key(catalog, "Catalog"),
                    _resource_key(schema, "Schema"),
                ).data
                node_volumes = []
                for summary in _sorted_named_resources(volume_items, "Volume"):
                    detail = volumes.get_volume(
                        instance_id, _resource_key(summary, "Volume")
                    ).data
                    volume_type = getattr(detail, "volume_type", None)
                    if external_only and volume_type != "EXTERNAL":
                        continue
                    if matched_count == max_results:
                        is_truncated = True
                        break
                    node_volumes.append(_volume_summary(detail))
                    matched_count += 1
                if node_volumes:
                    schema_nodes.append(
                        {
                            "display_name": getattr(schema, "display_name", None),
                            "volumes": node_volumes,
                        }
                    )
                if is_truncated:
                    break
        return {
            "catalog_name": getattr(catalog, "display_name", None),
            "external_only": external_only,
            "schemas": schema_nodes,
            "is_truncated": is_truncated,
        }

    def list_volume_files(
        self, catalog_name, schema_name, volume_name, path="/", max_results=100
    ):
        """List a bounded recursive folder and file tree in one volume.

        Args:
            catalog_name: Exact, case-sensitive catalog display name.
            schema_name: Exact, case-sensitive schema display name.
            volume_name: Exact, case-sensitive volume display name.
            path: Absolute volume path from which to recursively list entries.
            max_results: Maximum returned files and folders.

        Returns:
            dict: Sanitized recursive hierarchy and truncation state.

        Raises:
            AidpError: The selection/path is invalid, ambiguous, or inaccessible.
        """
        catalog_name = validate_resource_name(catalog_name, "Catalog")
        schema_name = validate_resource_name(schema_name, "Schema")
        volume_name = validate_resource_name(volume_name, "Volume")
        path = validate_volume_path(path)
        _validate_result_limit(max_results, MAX_VOLUME_FILE_RESULTS)
        entries = []
        page = None
        is_truncated = False
        with self._catalog_clients() as clients:
            instance_id, catalogs, schemas, volumes = clients
            catalog = _find_exact_catalog(catalogs, instance_id, catalog_name)
            schema = _find_exact_schema(
                schemas, instance_id, _resource_key(catalog, "Catalog"), schema_name
            )
            volume = _find_exact_volume(
                volumes,
                instance_id,
                _resource_key(catalog, "Catalog"),
                _resource_key(schema, "Schema"),
                volume_name,
            )
            volume_root = _volume_mount_path(catalog, schema, volume)
            while len(entries) < max_results:
                response = volumes.list_files(
                    instance_id,
                    _resource_key(volume, "Volume"),
                    path,
                    is_recursive=True,
                    limit=max_results - len(entries),
                    page=page,
                    sort_by="displayName",
                    sort_order="ASC",
                )
                items = getattr(response.data, "items", None) or []
                for index, item in enumerate(items):
                    entries.append(_volume_file_summary(item, path, volume_root))
                    if len(entries) == max_results:
                        is_truncated = index < len(items) - 1
                        break
                page = (getattr(response, "headers", None) or {}).get("opc-next-page")
                if is_truncated or not page:
                    break
            is_truncated = is_truncated or bool(page)
        return {
            "volume": {
                "catalog_name": getattr(catalog, "display_name", None),
                "schema_name": getattr(schema, "display_name", None),
                "display_name": getattr(volume, "display_name", None),
                "volume_key": _resource_key(volume, "Volume"),
            },
            "path": path,
            "root": _volume_file_tree(path, entries),
            "is_truncated": is_truncated,
        }

    def upload_notebook(self, local_path, workspace_path, overwrite=False, apply=False):
        """Plan or copy a local notebook to an AI DP workspace.

        Args:
            local_path: Local notebook path under the repository root.
            workspace_path: Destination notebook path relative to the workspace root.
            overwrite: Allow replacement of existing remote content.
            apply: Submit the update after the plan is reported.

        Returns:
            dict: Sanitized upload plan or result.

        Raises:
            AidpError: Validation or AI DP discovery/upload fails.
        """
        local_file, content, digest = validate_local_notebook(local_path)
        destination = validate_workspace_path(workspace_path)
        service_path = workspace_content_path(destination)
        with self._clients() as clients:
            instance_id, workspace_key, _, notebooks, _ = clients
            try:
                remote = notebook_content_request(
                    notebooks,
                    instance_id=instance_id,
                    workspace_key=workspace_key,
                    method="GET",
                    content_path=service_path,
                ).data
                remote_digest = getattr(remote, "hash", None)
                same = (
                    getattr(remote, "hash_algorithm", "").lower().replace("-", "")
                    == "sha256"
                    and remote_digest == digest
                )
                action = "unchanged" if same else "update"
            except oci.exceptions.ServiceError as exc:
                if not is_missing_content_error(exc):
                    raise
                action = "create"
            result = {
                "action": action,
                "apply": apply,
                "local_path": str(local_file.relative_to(PROJECT_ROOT)),
                "workspace_path": destination,
                "sha256": digest,
            }
            if not apply or action == "unchanged":
                return result
            if action == "update" and not overwrite:
                raise AidpError(
                    "Remote notebook exists; set overwrite=true to replace it."
                )
            if action == "create":
                create_workspace_folder(
                    notebooks,
                    instance_id,
                    workspace_key,
                    str(PurePosixPath(service_path).parent),
                )
                created = notebook_content_request(
                    notebooks,
                    instance_id=instance_id,
                    workspace_key=workspace_key,
                    method="POST",
                    content_path=str(PurePosixPath(service_path).parent),
                    body=models.CreateContentDetails(ext=".ipynb", type="notebook"),
                ).data
                created_path = getattr(created, "path", None)
                if not isinstance(created_path, str) or not created_path:
                    raise AidpError("Notebook creation response is missing its path.")
                notebook_content_request(
                    notebooks,
                    instance_id=instance_id,
                    workspace_key=workspace_key,
                    method="PATCH",
                    content_path=created_path,
                    body=models.ModifyContentDetails(path=service_path),
                )
            notebook_content_request(
                notebooks,
                instance_id=instance_id,
                workspace_key=workspace_key,
                method="PUT",
                content_path=service_path,
                body=models.UpdateContentDetails(
                    name=PurePosixPath(destination).name,
                    path=service_path,
                    type=models.UpdateContentDetails.TYPE_NOTEBOOK,
                    content=content,
                    format=models.UpdateContentDetails.FORMAT_JSON,
                ),
            )
            return result

    def list_notebooks(self, path="/Workspace", name_contains=None, max_results=100):
        """List a workspace directory's notebook summaries without content.

        Args:
            path: Absolute workspace directory, rooted at ``/Workspace``.
            name_contains: Optional case-insensitive substring to match locally.
            max_results: Maximum notebook summaries returned across OCI pages.

        Returns:
            dict: Sanitized notebook summaries and truncation metadata.

        Raises:
            AidpError: The inputs are unsafe or the AI DP request fails.
        """
        directory = validate_workspace_directory(path)
        if name_contains is not None and not isinstance(name_contains, str):
            raise AidpError("name_contains must be a string or null.")
        if not isinstance(max_results, int) or isinstance(max_results, bool):
            raise AidpError("max_results must be an integer from 1 through 1000.")
        if not 1 <= max_results <= MAX_NOTEBOOK_LIST_RESULTS:
            raise AidpError("max_results must be an integer from 1 through 1000.")

        match = name_contains.casefold() if name_contains else None
        summaries = []
        page = None
        with self._clients() as clients:
            instance_id, workspace_key, _, notebooks, _ = clients
            while len(summaries) < max_results:
                response = workspace_objects_request(
                    notebooks,
                    instance_id=instance_id,
                    workspace_key=workspace_key,
                    path=directory,
                    limit=max_results - len(summaries),
                    page=page,
                )
                for item in getattr(response.data, "items", None) or []:
                    display_name = getattr(item, "display_name", None)
                    if match and (
                        not isinstance(display_name, str)
                        or match not in display_name.casefold()
                    ):
                        continue
                    summaries.append(_notebook_summary(item))
                    if len(summaries) == max_results:
                        break
                page = (getattr(response, "headers", None) or {}).get("opc-next-page")
                if not page:
                    break
        return {
            "path": directory,
            "name_contains": name_contains,
            "notebooks": summaries,
            "is_truncated": bool(page),
        }

    def find_notebook_jobs(self, workspace_notebook_path, max_results=100):
        """Find workflow jobs with a workspace task for one exact notebook.

        Args:
            workspace_notebook_path: Absolute notebook path rooted at
                ``/Workspace``.
            max_results: Maximum matching jobs returned across OCI pages.

        Returns:
            dict: Sanitized matching job and task metadata with truncation state.

        Raises:
            AidpError: The inputs are unsafe or AI DP job discovery fails.
        """
        notebook_path = validate_workspace_notebook_path(workspace_notebook_path)
        if (
            isinstance(max_results, bool)
            or not isinstance(max_results, int)
            or not 1 <= max_results <= MAX_NOTEBOOK_JOB_SEARCH_RESULTS
        ):
            raise AidpError("max_results must be an integer from 1 through 1000.")

        matches = []
        page = None
        with self._clients() as clients:
            instance_id, workspace_key, _, _, workflows = clients
            while len(matches) < max_results:
                response = workflows.list_jobs(
                    instance_id,
                    workspace_key,
                    limit=max_results - len(matches),
                    page=page,
                )
                for summary in getattr(response.data, "items", None) or []:
                    job_key = _resource_key(summary, "Job")
                    job = workflows.get_job(instance_id, workspace_key, job_key).data
                    matching_tasks = _matching_notebook_tasks(job, notebook_path)
                    if not matching_tasks:
                        continue
                    matches.append(
                        {
                            "job_key": job_key,
                            "job_name": getattr(job, "name", None),
                            "job_path": getattr(job, "path", None),
                            "matching_tasks": matching_tasks,
                        }
                    )
                    if len(matches) == max_results:
                        break
                page = (getattr(response, "headers", None) or {}).get("opc-next-page")
                if not page:
                    break
        return {
            "workspace_notebook_path": notebook_path,
            "jobs": matches,
            "is_truncated": bool(page) or len(matches) == max_results,
        }

    def list_job_runs(self, job_name=None, job_key=None, max_results=25):
        """List bounded, newest-first run summaries for one workflow job.

        Exactly one job selector is required. A name is resolved to an exact
        visible job before the run query; a key is used directly after local
        validation.

        Args:
            job_name: Exact workflow job name, as an alternative to ``job_key``.
            job_key: Existing workflow job key, as an alternative to ``job_name``.
            max_results: Maximum run summaries returned across OCI pages.

        Returns:
            dict: Selected job reference, sanitized run summaries, and a
            truncation indicator.

        Raises:
            AidpError: The selectors are invalid or ambiguous, or the AI DP
                request fails.
        """
        if (job_name is None) == (job_key is None):
            raise AidpError("Provide exactly one of job_name or job_key.")
        if job_name is not None and (
            not isinstance(job_name, str) or not job_name.strip()
        ):
            raise AidpError("job_name must be a nonempty string.")
        if job_key is not None:
            validate_resource_key(job_key)
        if (
            isinstance(max_results, bool)
            or not isinstance(max_results, int)
            or not 1 <= max_results <= MAX_JOB_RUN_LIST_RESULTS
        ):
            raise AidpError("max_results must be an integer from 1 through 1000.")

        page = None
        runs = []
        with self._clients() as clients:
            instance_id, workspace_key, _, _, workflows = clients
            if job_name is not None:
                job = _find_job(workflows, instance_id, workspace_key, job_name)
                if job is None:
                    raise AidpError(f"No visible job is named {job_name!r}.")
                selected_job_key = _resource_key(job.data, "Job")
                selected_job_name = getattr(job.data, "name", job_name)
            else:
                selected_job_key = job_key
                selected_job_name = None

            while len(runs) < max_results:
                response = workflows.list_job_runs(
                    instance_id,
                    workspace_key,
                    job_key=[selected_job_key],
                    limit=min(25, max_results - len(runs)),
                    page=page,
                    sort_by="timeCreated",
                    sort_order="DESC",
                )
                for item in getattr(response.data, "items", None) or []:
                    runs.append(_run_response(item, _resource_key(item, "Job run")))
                    if len(runs) == max_results:
                        break
                page = (getattr(response, "headers", None) or {}).get("opc-next-page")
                if not page:
                    break

        return {
            "job_key": selected_job_key,
            "job_name": selected_job_name,
            "job_runs": runs,
            "is_truncated": bool(page),
        }

    def ensure_notebook_job(
        self,
        job_name,
        workspace_notebook_path,
        cluster_name,
        *,
        job_location="/Workspace/jobs",
        max_concurrent_runs=1,
        apply=False,
    ):
        """Plan or reconcile a managed single-notebook workflow job.

        Args:
            job_name: Exact workflow job name.
            workspace_notebook_path: Existing workspace notebook path.
            cluster_name: Exact active cluster name.
            job_location: Workspace path where AI DP stores the job definition.
            max_concurrent_runs: Positive job concurrency limit.
            apply: Submit the create or update after the plan is reported.

        Returns:
            dict: Sanitized job plan or result.

        Raises:
            AidpError: The requested job is unsafe or cannot be reconciled.
        """
        if not isinstance(job_name, str) or not job_name.strip():
            raise AidpError("Job name must be nonempty.")
        if not job_name.replace("_", "").isalnum() or not job_name[0].isalpha():
            raise AidpError(
                "Job name must start with a letter and contain only letters, "
                "numbers, or underscores."
            )
        if not isinstance(max_concurrent_runs, int) or max_concurrent_runs < 1:
            raise AidpError("max_concurrent_runs must be a positive integer.")
        notebook_path = validate_workspace_path(workspace_notebook_path)
        if (
            not job_location.startswith("/Workspace/")
            or ".." in PurePosixPath(job_location).parts
        ):
            raise AidpError("Job location must be below /Workspace.")
        with self._clients() as clients:
            instance_id, workspace_key, clusters, _, workflows = clients
            target = find_cluster(clusters, instance_id, workspace_key, cluster_name)
            response = _find_job(workflows, instance_id, workspace_key, job_name)
            action = "create" if response is None else "update"
            if response is not None and not _is_supported_job(response.data):
                raise AidpError("Existing job is not a managed single notebook task.")
            result = {
                "action": action,
                "apply": apply,
                "job_name": job_name,
                "workspace_path": notebook_path,
                "cluster_name": cluster_name,
            }
            if not apply:
                return result
            details = {
                "name": job_name,
                "description": "Managed by the AI DP MCP server.",
                "path": job_location,
                "max_concurrent_runs": max_concurrent_runs,
                "job_clusters": [models.JobCluster(cluster_key=target.cluster_key)],
                "tasks": [_managed_task(notebook_path, target.cluster_key)],
            }
            if response is None:
                created = workflows.create_job(
                    instance_id,
                    workspace_key,
                    models.CreateJobDetails(**details),
                    retry_strategy=oci.retry.NoneRetryStrategy(),
                )
                job_key = _resource_key(created.data, "Job")
                workflows.update_job(
                    instance_id,
                    workspace_key,
                    job_key,
                    models.UpdateJobDetails(**details),
                    retry_strategy=oci.retry.NoneRetryStrategy(),
                )
            else:
                job_key = _resource_key(response.data, "Job")
                workflows.update_job(
                    instance_id,
                    workspace_key,
                    job_key,
                    models.UpdateJobDetails(**details),
                    if_match=response.headers.get("etag"),
                    retry_strategy=oci.retry.NoneRetryStrategy(),
                )
            result["job_key"] = job_key
            return result

    def start_notebook_job(
        self, job_name, *, wait=False, timeout_seconds=1200, confirm_start=False
    ):
        """Start a compatible notebook job and optionally poll its run.

        Args:
            job_name: Exact managed workflow job name.
            wait: Poll the run until a terminal state or timeout.
            timeout_seconds: Positive wait limit in seconds.
            confirm_start: Required explicit authorization for compute use.

        Returns:
            dict: Job-run key and latest known state.

        Raises:
            AidpError: Confirmation, job shape, cluster state, or polling fails.
        """
        if not confirm_start:
            raise AidpError(
                "Set confirm_start=true to submit a compute-consuming job run."
            )
        if not isinstance(timeout_seconds, int) or timeout_seconds < 1:
            raise AidpError("timeout_seconds must be a positive integer.")
        with self._clients() as clients:
            instance_id, workspace_key, clusters, _, workflows = clients
            response = _find_job(workflows, instance_id, workspace_key, job_name)
            if response is None or not _is_supported_job(response.data):
                raise AidpError("Job must be an existing managed single notebook task.")
            task = response.data.tasks[0]
            cluster_key = getattr(getattr(task, "cluster", None), "cluster_key", None)
            validate_resource_key(cluster_key)
            cluster = clusters.get_cluster(instance_id, workspace_key, cluster_key).data
            if getattr(cluster, "state", None) != "ACTIVE":
                raise AidpError("The job cluster must be ACTIVE before submission.")
            job_key = _resource_key(response.data, "Job")
            created = workflows.create_job_run(
                instance_id,
                workspace_key,
                models.CreateJobRunDetails(job_key=job_key, parameters=[]),
                retry_strategy=oci.retry.NoneRetryStrategy(),
            )
            run_key = _resource_key(created.data, "Job run")
            result = {
                "job_name": job_name,
                "job_run_key": run_key,
                "state": "SUBMITTED",
            }
            if not wait:
                return result
            return self._wait_for_run(
                workflows, instance_id, workspace_key, run_key, timeout_seconds
            )

    def _wait_for_run(
        self, workflows, instance_id, workspace_key, run_key, timeout_seconds
    ):
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            response = workflows.get_job_run(instance_id, workspace_key, run_key)
            state = _run_state(response.data)
            if state in TERMINAL_JOB_STATES:
                return _run_response(response.data, run_key)
            time.sleep(min(10, max(0.1, deadline - time.monotonic())))
        latest = workflows.get_job_run(instance_id, workspace_key, run_key).data
        result = _run_response(latest, run_key)
        result["timed_out"] = True
        return result

    def get_job_run(self, job_run_key):
        """Read a job run status without fetching logs or notebook output.

        Args:
            job_run_key: Existing AI DP job-run key.

        Returns:
            dict: Sanitized job-run status.
        """
        validate_resource_key(job_run_key)
        with self._clients() as clients:
            instance_id, workspace_key, _, _, workflows = clients
            response = workflows.get_job_run(instance_id, workspace_key, job_run_key)
            return _run_response(response.data, job_run_key)

    def get_cluster_status(self, cluster_name):
        """Read a sanitized summary for one exact configured-workspace cluster.

        Args:
            cluster_name: Exact cluster display name in the configured workspace.

        Returns:
            dict: Cluster state and selected non-sensitive configuration fields.

        Raises:
            AidpError: The cluster is absent, ambiguous, or has an invalid name.
        """
        if not isinstance(cluster_name, str) or not cluster_name.strip():
            raise AidpError("Cluster name must be a nonempty string.")
        with self._clients() as clients:
            instance_id, workspace_key, clusters, _, _ = clients
            cluster = find_cluster_status(
                clusters, instance_id, workspace_key, cluster_name
            )
            return _cluster_response(cluster)

    def set_cluster_state(
        self,
        cluster_name,
        action,
        *,
        wait=False,
        timeout_seconds=1200,
        confirm_action=False,
    ):
        """Start or stop one exact cluster in the configured workspace.

        Args:
            cluster_name: Exact cluster display name in the configured workspace.
            action: Requested lifecycle action, either ``start`` or ``stop``.
            wait: Poll until the requested state is observed.
            timeout_seconds: Positive maximum polling duration in seconds.
            confirm_action: Required explicit authorization for the mutation.

        Returns:
            dict: Sanitized cluster summary and lifecycle submission outcome.

        Raises:
            AidpError: Validation, state transition, submission, or polling fails.
        """
        if not confirm_action:
            raise AidpError(
                "Set confirm_action=true to submit a cluster lifecycle action."
            )
        if not isinstance(cluster_name, str) or not cluster_name.strip():
            raise AidpError("Cluster name must be a nonempty string.")
        if action not in ("start", "stop"):
            raise AidpError("Cluster action must be either 'start' or 'stop'.")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or timeout_seconds < 1
        ):
            raise AidpError("timeout_seconds must be a positive integer.")
        with self._clients() as clients:
            instance_id, workspace_key, clusters, _, _ = clients
            response = find_cluster_details(
                clusters, instance_id, workspace_key, cluster_name
            )
            cluster = response.data
            state = getattr(cluster, "state", None)
            desired, origin, transition = (
                ("ACTIVE", "STOPPED", "STARTING")
                if action == "start"
                else ("STOPPED", "ACTIVE", "STOPPING")
            )
            if state == desired:
                return _cluster_lifecycle_response(action, "already_desired", cluster)
            if state not in (origin, transition):
                raise AidpError(
                    f"Cannot {action} a cluster in state {state!r}; inspect its status."
                )
            if state == origin:
                _submit_cluster_action(
                    clusters,
                    instance_id=instance_id,
                    workspace_key=workspace_key,
                    cluster=cluster,
                    action=action,
                    headers=response.headers,
                )
                outcome = "accepted"
            else:
                outcome = "already_transitioning"
            if not wait:
                return _cluster_lifecycle_response(action, outcome, cluster)
            return self._wait_for_cluster_state(
                clusters,
                instance_id=instance_id,
                workspace_key=workspace_key,
                cluster_key=_resource_key(cluster, "Cluster"),
                action=action,
                desired=desired,
                origin=origin,
                transition=transition,
                timeout_seconds=timeout_seconds,
            )

    def _wait_for_cluster_state(
        self,
        clusters,
        *,
        instance_id,
        workspace_key,
        cluster_key,
        action,
        desired,
        origin,
        transition,
        timeout_seconds,
    ):
        """Poll a submitted lifecycle action without cancelling it on timeout."""
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            cluster = clusters.get_cluster(instance_id, workspace_key, cluster_key).data
            state = getattr(cluster, "state", None)
            if state == desired:
                return _cluster_lifecycle_response(action, "completed", cluster)
            if state not in (origin, transition):
                raise AidpError(
                    "Cluster reached an unexpected state while waiting; inspect its "
                    "status."
                )
            time.sleep(min(10, max(0.1, deadline - time.monotonic())))
        cluster = clusters.get_cluster(instance_id, workspace_key, cluster_key).data
        result = _cluster_lifecycle_response(action, "timed_out", cluster)
        result["timed_out"] = True
        return result

    def get_job_run_output(
        self, job_run_key, max_characters=MAX_JOB_RUN_OUTPUT_CHARACTERS
    ):
        """Fetch bounded plain-text output for a managed single-task job run.

        Args:
            job_run_key: Existing AI DP job-run key.
            max_characters: Combined output and error-trace character limit.

        Returns:
            dict: Sanitized task output metadata and bounded plain text.

        Raises:
            AidpError: The run is not a single-task run or has no output yet.
        """
        validate_resource_key(job_run_key)
        if (
            isinstance(max_characters, bool)
            or not isinstance(max_characters, int)
            or not 1 <= max_characters <= MAX_JOB_RUN_OUTPUT_CHARACTERS
        ):
            raise AidpError(
                "max_characters must be an integer from 1 to "
                f"{MAX_JOB_RUN_OUTPUT_CHARACTERS}."
            )
        with self._clients() as clients:
            instance_id, workspace_key, _, _, workflows = clients
            task_runs = oci.pagination.list_call_get_all_results(
                workflows.list_task_runs,
                instance_id,
                workspace_key,
                job_run_key,
                # AI DP rejects an unspecified sortBy value as null. Specify a
                # documented field so the SDK request is accepted consistently.
                sort_by="timeCreated",
            ).data
            if len(task_runs) != 1:
                raise AidpError("Job run must contain exactly one task run.")
            task_run = task_runs[0]
            task_run_key = _resource_key(task_run, "Task run")
            output_key = getattr(task_run, "output_key", None)
            if not isinstance(output_key, str) or not output_key:
                raise AidpError("Task run has no available output key.")
            output = workflows.fetch_output(
                instance_id,
                workspace_key,
                task_run_key,
                models.FetchOutputDetails(output_key=output_key),
            ).data
            return _task_run_output_response(
                job_run_key, task_run, output, max_characters
            )


def _validate_result_limit(value, maximum):
    """Validate a bounded MCP collection result limit."""
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        raise AidpError(f"max_results must be an integer from 1 through {maximum}.")


def _sorted_named_resources(resources, label):
    """Validate and sort remote resource summaries deterministically."""
    checked = []
    for resource in resources:
        display_name = getattr(resource, "display_name", None)
        if not isinstance(display_name, str) or not display_name:
            raise AidpError(f"{label} response is missing its display name.")
        _resource_key(resource, label)
        checked.append(resource)
    return sorted(
        checked,
        key=lambda item: (
            item.display_name.casefold(),
            item.display_name,
            getattr(item, "key"),
        ),
    )


def _find_exact_catalog(catalogs, instance_id, catalog_name):
    """Resolve exactly one visible catalog by its display name."""
    items = oci.pagination.list_call_get_all_results(
        catalogs.list_catalogs, instance_id, display_name=catalog_name
    ).data
    matches = [
        item
        for item in _sorted_named_resources(items, "Catalog")
        if item.display_name == catalog_name
    ]
    if len(matches) != 1:
        raise AidpError(
            f"Catalog name has {len(matches)} visible exact matches; select a unique "
            "catalog."
        )
    return matches[0]


def _find_exact_schema(schemas, instance_id, catalog_key, schema_name):
    """Resolve exactly one schema in a selected catalog."""
    items = oci.pagination.list_call_get_all_results(
        schemas.list_schemas, instance_id, catalog_key, display_name=schema_name
    ).data
    matches = [
        item
        for item in _sorted_named_resources(items, "Schema")
        if item.display_name == schema_name
    ]
    if len(matches) != 1:
        raise AidpError(
            f"Schema name has {len(matches)} visible exact matches in the catalog."
        )
    return matches[0]


def _find_exact_volume(volumes, instance_id, catalog_key, schema_key, volume_name):
    """Resolve exactly one volume in a selected schema."""
    items = oci.pagination.list_call_get_all_results(
        volumes.list_volumes,
        instance_id,
        catalog_key,
        schema_key,
        display_name=volume_name,
    ).data
    matches = [
        item
        for item in _sorted_named_resources(items, "Volume")
        if item.display_name == volume_name
    ]
    if len(matches) != 1:
        raise AidpError(
            f"Volume name has {len(matches)} visible exact matches in the schema."
        )
    return matches[0]


def _volume_summary(volume):
    """Return non-sensitive volume metadata needed for exploration."""
    volume_type = getattr(volume, "volume_type", None)
    return {
        "display_name": getattr(volume, "display_name", None),
        "volume_key": _resource_key(volume, "Volume"),
        "full_name": getattr(volume, "full_name", None),
        "volume_type": volume_type,
        "storage_location": (
            getattr(volume, "storage_location", None)
            if volume_type == "EXTERNAL"
            else None
        ),
        "lifecycle_state": getattr(volume, "lifecycle_state", None),
    }


def _volume_mount_path(catalog, schema, volume):
    """Build the AI DP mount path for one resolved volume."""
    labels = (
        getattr(catalog, "display_name", None),
        getattr(schema, "display_name", None),
        getattr(volume, "display_name", None),
    )
    if any(
        not isinstance(label, str)
        or not label
        or PurePosixPath(label).parts != (label,)
        for label in labels
    ):
        raise AidpError("Resolved volume names cannot form a safe mount path.")
    return str(PurePosixPath("/Volumes").joinpath(*labels))


def _volume_file_summary(item, logical_root, volume_root):
    """Sanitize one item and normalize an optional AI DP mount-path prefix."""
    path = getattr(item, "path", None)
    if not isinstance(path, str):
        raise AidpError("Volume file response is missing its path.")
    normalized_path = validate_volume_path(path)
    candidate = PurePosixPath(normalized_path)
    mount_root = PurePosixPath(volume_root)
    try:
        candidate = PurePosixPath("/").joinpath(
            *candidate.relative_to(mount_root).parts
        )
    except ValueError:
        # Some AI DP responses already use a path relative to the volume.
        pass
    root = PurePosixPath(logical_root)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise AidpError("Volume file response is outside the requested path.") from exc
    entry_type = getattr(item, "type", None)
    if entry_type not in ("FILE", "FOLDER"):
        raise AidpError("Volume file response has an unsupported item type.")
    display_name = getattr(item, "display_name", None)
    if not isinstance(display_name, str) or not display_name:
        raise AidpError("Volume file response is missing its display name.")
    return {
        "display_name": display_name,
        "path": str(candidate),
        "type": entry_type,
        "time_created": getattr(item, "time_created", None),
        "time_updated": getattr(item, "time_updated", None),
    }


def _volume_file_tree(root_path, entries):
    """Build an ordered hierarchy from sanitized recursive file entries."""
    root = {
        "display_name": PurePosixPath(root_path).name or "/",
        "path": root_path,
        "type": "FOLDER",
        "children": {},
    }
    root_posix = PurePosixPath(root_path)
    for entry in entries:
        relative_parts = PurePosixPath(entry["path"]).relative_to(root_posix).parts
        if not relative_parts:
            continue
        node = root
        parent_parts = relative_parts[:-1]
        for index, part in enumerate(parent_parts):
            parent_path = str(root_posix.joinpath(*relative_parts[: index + 1]))
            child = node["children"].setdefault(
                part,
                {
                    "display_name": part,
                    "path": parent_path,
                    "type": "FOLDER",
                    "inferred": True,
                    "children": {},
                },
            )
            node = child
        leaf_name = relative_parts[-1]
        current = node["children"].get(leaf_name)
        if current and current.get("inferred") and entry["type"] == "FOLDER":
            current.update(entry)
            current.pop("inferred", None)
        else:
            node["children"][leaf_name] = {**entry, "children": {}}

    def render(node):
        children = node.pop("children")
        if node["type"] == "FOLDER":
            ordered = sorted(
                children.values(),
                key=lambda child: (
                    child["type"] != "FOLDER",
                    child["display_name"].casefold(),
                    child["display_name"],
                ),
            )
            node["children"] = [render(child) for child in ordered]
        return node

    return render(root)


def _run_state(job_run):
    state = getattr(job_run, "state", None)
    return getattr(state, "status", state)


def _run_response(job_run, run_key):
    state = getattr(job_run, "state", None)
    return {
        "job_run_key": run_key,
        "state": _run_state(job_run),
        "state_message": getattr(state, "state_message", None),
        "start_time": str(getattr(job_run, "start_time", "")) or None,
        "end_time": str(getattr(job_run, "end_time", "")) or None,
    }


def _cluster_response(cluster):
    """Return an intentionally small, non-sensitive cluster summary."""
    return {
        "cluster_key": _resource_key(cluster, "Cluster"),
        "display_name": getattr(cluster, "display_name", None),
        "type": getattr(cluster, "type", None),
        "state": getattr(cluster, "state", None),
        "state_details": getattr(cluster, "state_details", None),
        "runtime_version": getattr(
            getattr(cluster, "cluster_runtime_config", None), "runtime_version", None
        ),
        "node_type": getattr(cluster, "node_type", None),
        "driver": _shape_response(getattr(cluster, "driver_config", None)),
        "workers": _worker_response(getattr(cluster, "worker_config", None)),
        "auto_termination_minutes": getattr(cluster, "auto_termination_minutes", None),
    }


def _notebook_summary(item):
    """Return selected notebook metadata without creator, tags, or content."""
    return {
        "display_name": getattr(item, "display_name", None),
        "path": getattr(item, "path", None),
        "type": getattr(item, "type", None),
        "time_created": getattr(item, "time_created", None),
        "time_updated": getattr(item, "time_updated", None),
    }


def _submit_cluster_action(
    clusters, *, instance_id, workspace_key, cluster, action, headers
):
    """Submit one lifecycle request without application-level retries.

    A transport error has an unknown remote outcome, so callers must inspect
    status rather than automatically submitting a second request.
    """
    options = {
        "retry_strategy": oci.retry.NoneRetryStrategy(),
        "opc_retry_token": str(uuid4()),
    }
    etag = headers.get("etag") if headers else None
    if etag:
        options["if_match"] = etag
    try:
        if action == "start":
            response = clusters.start_cluster(
                instance_id,
                workspace_key,
                _resource_key(cluster, "Cluster"),
                models.StartClusterDetails(),
                **options,
            )
        else:
            response = clusters.stop_cluster(
                instance_id,
                workspace_key,
                _resource_key(cluster, "Cluster"),
                models.StopClusterDetails(),
                **options,
            )
    except oci.exceptions.RequestException as exc:
        raise AidpError(
            "Cluster action outcome is unknown; check status before retrying."
        ) from exc
    if response.status != 202:
        raise AidpError(
            "Unexpected cluster action response; check status before retrying."
        )


def _cluster_lifecycle_response(action, outcome, cluster):
    """Return a lifecycle result without response bodies or trace metadata."""
    return {
        "action": action,
        "outcome": outcome,
        "cluster": _cluster_response(cluster),
    }


def _shape_response(configuration):
    """Return selected driver shape fields without serializing SDK objects."""
    if configuration is None:
        return None
    shape_config = getattr(configuration, "driver_shape_config", None)
    return {
        "node_type": getattr(configuration, "driver_node_type", None),
        "shape": getattr(configuration, "driver_shape", None),
        "ocpus": getattr(shape_config, "ocpus", None),
        "memory_in_gbs": getattr(shape_config, "memory_in_gbs", None),
    }


def _worker_response(configuration):
    """Return selected worker-count and shape fields without SDK internals."""
    if configuration is None:
        return None
    shape_config = getattr(configuration, "worker_shape_config", None)
    return {
        "shape": getattr(configuration, "worker_shape", None),
        "ocpus": getattr(shape_config, "ocpus", None),
        "memory_in_gbs": getattr(shape_config, "memory_in_gbs", None),
        "min_worker_count": getattr(configuration, "min_worker_count", None),
        "max_worker_count": getattr(configuration, "max_worker_count", None),
    }


def _task_run_output_response(job_run_key, task_run, output, max_characters):
    """Filter and bound task output returned to the MCP client."""
    remaining = max_characters
    locally_truncated = False
    error_trace, remaining, error_trace_truncated = _take_text(
        getattr(output, "error_trace", None), remaining
    )
    locally_truncated = error_trace_truncated
    text_output = []
    for item in getattr(output, "data", None) or []:
        if (
            getattr(item, "type", None) != "TEXT_PLAIN"
            or getattr(item, "is_base64", False)
            or getattr(item, "compression", None)
        ):
            continue
        text, remaining, truncated = _take_text(getattr(item, "value", None), remaining)
        locally_truncated = locally_truncated or truncated
        if text is not None:
            text_output.append({"type": "TEXT_PLAIN", "text": text})
    return {
        "job_run_key": job_run_key,
        "task_run_key": _resource_key(task_run, "Task run"),
        "task_key": getattr(task_run, "task_key", None),
        "output_key": getattr(output, "key", None),
        "task_type": getattr(output, "task_type", None),
        "text_output": text_output,
        "error_trace": error_trace,
        "is_truncated": bool(getattr(output, "is_truncated", False))
        or locally_truncated,
    }


def _take_text(value, remaining):
    """Return at most the remaining text characters and an exhaustion flag."""
    if not isinstance(value, str):
        return None, remaining, False
    if len(value) <= remaining:
        return value, remaining - len(value), False
    return value[:remaining], 0, True
