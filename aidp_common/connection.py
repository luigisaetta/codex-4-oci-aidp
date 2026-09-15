"""
Author: L. Saetta
Date last modified: 2026-09-15
License: MIT
Description: Shared OCI authentication, discovery and managed SDK clients.
"""

import os
import oci


class AidpError(Exception):
    """An actionable discovery, API, or lifecycle failure."""


def validate_resource_key(value):
    """Reject path separators that OCI 2.165.1 does not escape in path parameters.

    Args:
        value: Resource identifier used as one URL path segment.

    Raises:
        AidpError: The identifier is empty or contains path traversal syntax.
    """
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(character in value for character in ("/", "\\"))
        or value in (".", "..")
    ):
        raise AidpError("Resource keys must be nonempty single path segments.")


def load_auth(args):
    """Load the selected API-key profile and return config plus SDK options.

    Args:
        args: Settings with config_file, profile and region attributes.

    Returns:
        A pair of OCI configuration and signer/timeout/retry client options.
    """
    config = oci.config.from_file(os.path.expanduser(args.config_file), args.profile)
    if args.region:
        config["region"] = args.region
    oci.config.validate_config(config)
    if config.get("security_token_file"):
        raise AidpError(
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
    return config, client_options


def managed_client(
    resources, client_type, config, options, *, preserve_timestamps=False
):
    """Register each SDK session so partial setup failures close it.

    Args:
        resources: ExitStack owning the client's session lifetime.
        client_type: OCI or Workbench SDK client constructor.
        config: Validated OCI profile dictionary.
        options: Signer, timeout, retry and optional endpoint settings.
        preserve_timestamps: Retain opaque Workbench date values without parsing.

    Returns:
        Initialized client with redirects disabled and registered cleanup.
    """
    client = client_type(config, **options)
    if preserve_timestamps:
        # Some Workbench deployments return numeric timestamps instead of ISO text.
        # Commands never interpret dates: retain values without guessing
        # units. Copy the mapping to avoid changing other SDK clients globally.
        client.base_client.type_mappings = {
            **client.base_client.type_mappings,
            "datetime": object,
        }
    client.base_client.session.max_redirects = 0
    resources.callback(client.base_client.session.close)
    return client


def resolve_compartment(identity, tenancy_id, value):
    """Resolve an OCID or an exact accessible active compartment name.

    Args:
        identity: OCI IdentityClient.
        tenancy_id: Root tenancy OCID from the selected profile.
        value: Compartment name, compartment OCID, or root tenancy OCID.

    Returns:
        The selected compartment OCID.

    Raises:
        AidpError: No unique name match is visible.
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
        raise AidpError(
            f"Compartment name has {len(matches)} visible matches; supply its OCID. "
            "For the root compartment, supply the tenancy OCID."
        )
    return matches[0]


def list_instances(control, compartment_id, instance_id=None):
    """Return active instances in the compartment, checking explicit selections.

    Args:
        control: OCI AiDataPlatformClient.
        compartment_id: Required compartment OCID.
        instance_id: Optional instance OCID to select instead of listing.

    Returns:
        List of active AI DP instances in the selected compartment.

    Raises:
        AidpError: The selected instance is inactive or outside the compartment.
    """
    if instance_id:
        instance = control.get_ai_data_platform(instance_id).data
        if (
            instance.compartment_id != compartment_id
            or instance.lifecycle_state != "ACTIVE"
        ):
            raise AidpError(
                "Selected instance must be active in the requested compartment."
            )
        instances = [instance]
    else:
        instances = oci.pagination.list_call_get_all_results(
            control.list_ai_data_platforms,
            compartment_id=compartment_id,
            lifecycle_state="ACTIVE",
        ).data
    return instances
