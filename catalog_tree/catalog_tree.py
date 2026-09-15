"""
Author: L. Saetta
Date last modified: 2026-09-15
License: MIT
Description: List visible schemas and volumes in an AI DP catalog as a tree.
"""

import sys
import time
import unicodedata
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Allow direct invocation from any working directory without installing this repo.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Imports follow the path bootstrap required by direct script execution.
# pylint: disable=wrong-import-position
import oci
from aidp_python_client.aidataplatform_dp import (
    CatalogClient,
    SchemaClient,
    VolumeClient,
)

from aidp_common.connection import (
    AidpError,
    list_instances,
    load_auth,
    managed_client,
    resolve_compartment,
    validate_resource_key,
)
from aidp_common.output import print_banner, report_unexpected_error
from aidp_common.settings import connection_parser, validate_connection


@dataclass(frozen=True)
class SchemaNode:
    """A schema display name and its ordered volume names."""

    name: str
    volumes: tuple[str, ...]


@dataclass(frozen=True)
class CatalogTree:
    """A fully retrieved catalog; rendering never performs network requests."""

    name: str
    schemas: tuple[SchemaNode, ...]


def parse_settings(argv=None):
    """Parse a required catalog name and shared OCI connection settings.

    Args:
        argv: CLI arguments, or None for process arguments.

    Returns:
        Namespace containing catalog name and validated connection settings.

    Raises:
        SystemExit: Help was requested or arguments are invalid.
    """
    parser, _, _ = connection_parser(
        argv, "List catalog schemas and volumes as a tree."
    )
    parser.add_argument("catalog_name", help="Exact catalog display name.")
    args = parser.parse_args(argv)
    validate_connection(args, parser)
    if not args.catalog_name.strip():
        parser.error("Catalog name must not be empty.")
    return args


def _check_resource(resource):
    # Catalog/schema keys are query parameters, so preserve qualified names as-is.
    if not isinstance(resource.key, str) or not resource.key.strip():
        raise AidpError("A resource response is missing its key.")
    if not isinstance(resource.display_name, str) or not resource.display_name.strip():
        raise AidpError("A resource response is missing its display name.")
    return resource


def _sorted_resources(resources):
    return sorted(
        (_check_resource(item) for item in resources),
        key=lambda item: (item.display_name.casefold(), item.display_name, item.key),
    )


def find_catalog(control, catalogs, compartment_id, name, instance_id=None):
    """Resolve one exact catalog match across active, visible instances.

    Args:
        control: OCI AiDataPlatformClient.
        catalogs: Generated CatalogClient.
        compartment_id: Selected compartment OCID.
        name: Exact catalog display name.
        instance_id: Optional instance OCID to narrow discovery.

    Returns:
        A pair containing the selected instance OCID and catalog summary.

    Raises:
        AidpError: No match, ambiguity, or malformed resource metadata.
    """
    matches = []
    for instance in list_instances(control, compartment_id, instance_id):
        validate_resource_key(instance.id)
        items = oci.pagination.list_call_get_all_results(
            catalogs.list_catalogs, instance.id, display_name=name
        ).data
        for catalog in items:
            if catalog.display_name == name:
                matches.append((instance.id, _check_resource(catalog)))
    if len(matches) != 1:
        raise AidpError(
            f"Catalog name has {len(matches)} visible matches. Check the name, "
            "region and compartment; use AIDP_INSTANCE_ID to narrow the scope."
        )
    return matches[0]


def read_tree(schemas, volumes, instance_id, catalog):
    """Retrieve every schema and volume before rendering any tree output.

    Args:
        schemas: Generated SchemaClient.
        volumes: Generated VolumeClient.
        instance_id: Selected instance OCID.
        catalog: Selected catalog summary, including its key and display name.

    Returns:
        A complete CatalogTree with deterministic alphabetical ordering.
    """
    items = oci.pagination.list_call_get_all_results(
        schemas.list_schemas, instance_id, catalog.key
    ).data
    nodes = []
    for schema in _sorted_resources(items):
        children = oci.pagination.list_call_get_all_results(
            volumes.list_volumes, instance_id, catalog.key, schema.key
        ).data
        nodes.append(
            SchemaNode(
                schema.display_name,
                tuple(volume.display_name for volume in _sorted_resources(children)),
            )
        )
    return CatalogTree(catalog.display_name, tuple(nodes))


def _label(value):
    return "".join(
        (
            char.encode("unicode_escape").decode("ascii")
            if unicodedata.category(char).startswith("C")
            else char
        )
        for char in value
    )


def render_tree(tree):
    """Render a hierarchy with safe labels and no network access.

    Args:
        tree: Complete CatalogTree with ordered schemas and volumes.

    Returns:
        Unicode tree text with control characters escaped in display names.
    """
    lines = [_label(tree.name)]
    for index, schema in enumerate(tree.schemas):
        last_schema = index == len(tree.schemas) - 1
        lines.append(("└── " if last_schema else "├── ") + _label(schema.name))
        prefix = "    " if last_schema else "│   "
        for volume_index, volume in enumerate(schema.volumes):
            branch = "└── " if volume_index == len(schema.volumes) - 1 else "├── "
            lines.append(prefix + branch + _label(volume))
    return "\n".join(lines)


def _execute(args):
    stage = "loading OCI authentication"
    try:
        config, options = load_auth(args)
        workbench_options = dict(options)
        if args.endpoint:
            workbench_options["service_endpoint"] = args.endpoint
        with ExitStack() as resources:
            stage = "initializing SDK clients"
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
            stage = "resolving the compartment"
            compartment = resolve_compartment(
                identity, config["tenancy"], args.compartment
            )
            stage = "finding the catalog"
            instance, catalog = find_catalog(
                control, catalogs, compartment, args.catalog_name, args.instance_id
            )
            stage = "listing schemas and volumes"
            tree = read_tree(schemas, volumes, instance, catalog)
        print(render_tree(tree), flush=True)
        return 0
    except AidpError as exc:
        print(f"Error while {stage}: {exc}", file=sys.stderr)
    except oci.exceptions.ServiceError as exc:
        print(
            f"OCI HTTP {exc.status} while {stage}; check resource visibility, "
            "permissions and API availability. No tree was printed.",
            file=sys.stderr,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        report_unexpected_error(exc, stage)
    return 1


# Each CLI owns its exception boundary and clock for independent execution.
# pylint: disable=duplicate-code
def main(argv=None):
    """Run a read-only catalog listing with timing banners on stderr.

    Args:
        argv: Optional CLI argument list.

    Returns:
        Exit code 0 for success, 1 for failure or 130 for interruption.
        Invalid arguments exit with code 2 before execution begins.
    """
    args = parse_settings(argv)
    started = time.monotonic()
    banner_options = {"title": "OCI AI DP catalog tree", "stream": sys.stderr}
    print_banner("LIST CATALOG", datetime.now(timezone.utc), **banner_options)
    try:
        return _execute(args)
    except KeyboardInterrupt:
        print("Interrupted; catalog listing was not completed.", file=sys.stderr)
        return 130
    finally:
        print_banner(
            "LIST CATALOG",
            datetime.now(timezone.utc),
            elapsed=time.monotonic() - started,
            **banner_options,
        )


if __name__ == "__main__":
    sys.exit(main())
