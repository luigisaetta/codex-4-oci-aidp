"""
Author: L. Saetta
Date last modified: 2026-09-15
License: MIT
Description: Load lifecycle settings and validate optional Workbench endpoint overrides.
"""

import argparse
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

DEFAULT_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def positive_int(value):
    """Parse a strictly positive integer for CLI or dotenv settings."""
    try:
        number = int(value)
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError("Value must be a positive integer.") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("Value must be a positive integer.")
    return number


def endpoint_origin(value):
    """Validate an HTTPS origin without credentials, paths or query parameters."""
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or any((parsed.username, parsed.password, parsed.query, parsed.fragment))
        or parsed.path not in ("", "/")
    ):
        raise argparse.ArgumentTypeError("Endpoint must be an HTTPS service origin.")
    return value.rstrip("/")


def _boolean(value):
    if value.lower() in ("true", "1", "yes"):
        return True
    if value.lower() in ("false", "0", "no"):
        return False
    raise argparse.ArgumentTypeError("Boolean settings must be true or false.")


def parse_settings(argv=None):
    """Load CLI > process environment > dotenv > defaults without altering os.environ.

    Args:
        argv: Optional argument list; defaults to process arguments.

    Returns:
        Validated settings; a missing endpoint lets the SDK resolve the region.

    Raises:
        SystemExit: Invalid or missing configuration (exit code 2).
    """
    bootstrap = argparse.ArgumentParser(add_help=False)
    bootstrap.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    initial, _ = bootstrap.parse_known_args(argv)
    env_path = Path(initial.env_file).expanduser()
    parser = argparse.ArgumentParser(
        parents=[bootstrap], description="Inspect, start or stop an OCI AI DP cluster."
    )
    try:
        values = {**dotenv_values(env_path, interpolate=False), **os.environ}
    except OSError:
        parser.error("Cannot read the selected .env file.")

    def setting(name, default=None):
        value = values.get(name)
        return value.strip() if value and value.strip() else default

    parser.add_argument(
        "action",
        nargs="?",
        choices=("start", "stop", "status"),
        default=setting("ACTION", "status"),
    )
    fields = {
        "compartment": ("COMPARTMENT", None),
        "cluster-name": ("CLUSTER_NAME", None),
        "config-file": ("OCI_CONFIG_FILE", "~/.oci/config"),
        "profile": ("OCI_PROFILE", "DEFAULT"),
        "region": ("REGION", "eu-frankfurt-1"),
        "instance-id": ("AIDP_INSTANCE_ID", None),
        "workspace-key": ("WORKSPACE_KEY", None),
        "workspace-name": ("WORKSPACE_NAME", None),
        "cluster-type": ("CLUSTER_TYPE", None),
        "endpoint": ("AIDP_ENDPOINT", None),
    }
    for flag, (variable, default) in fields.items():
        parser.add_argument(
            f"--{flag}",
            default=setting(variable, default),
            help=f"Override {variable} from .env.",
        )
    for flag, variable, default in (
        ("wait-timeout", "WAIT_TIMEOUT", "1200"),
        ("poll-interval", "POLL_INTERVAL", "10"),
    ):
        parser.add_argument(
            f"--{flag}", type=positive_int, default=setting(variable, default)
        )
    # BooleanOptionalAction supplies --no-wait and --no-dry-run for explicit overrides.
    for flag, variable in (("wait", "WAIT"), ("dry-run", "DRY_RUN")):
        parser.add_argument(
            f"--{flag}",
            action=argparse.BooleanOptionalAction,
            default=setting(variable, "false"),
        )
    args = parser.parse_args(argv)
    try:
        for name in ("wait", "dry_run"):
            value = getattr(args, name)
            setattr(args, name, _boolean(value) if isinstance(value, str) else value)
        for name in ("compartment", "cluster_name", "profile", "config_file", "region"):
            if not getattr(args, name) or not getattr(args, name).strip():
                parser.error(
                    f"Set {name.upper()} in {env_path.name} "
                    f"or pass --{name.replace('_', '-')}."
                )
        if args.action not in ("start", "stop", "status"):
            parser.error("ACTION must be start, stop or status.")
        if args.cluster_type not in (None, "USER", "AI_COMPUTE"):
            parser.error("CLUSTER_TYPE must be USER or AI_COMPUTE.")
        if args.workspace_key and args.workspace_name:
            parser.error("Use either WORKSPACE_KEY or WORKSPACE_NAME, not both.")
        if not re.fullmatch(r"[a-z]+(?:-[a-z0-9]+)+-\d+", args.region):
            parser.error("REGION must be an OCI region identifier.")
        args.endpoint = endpoint_origin(args.endpoint) if args.endpoint else None
        if not env_path.is_file() and initial.env_file != str(DEFAULT_ENV_FILE):
            parser.error("The explicitly selected .env file does not exist.")
    except (argparse.ArgumentTypeError, ValueError) as exc:
        parser.error(str(exc))
    return args
