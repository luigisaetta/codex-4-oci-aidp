"""
Author: L. Saetta
Date last modified: 2026-09-15
License: MIT
Description: Shared dotenv loading and OCI connection command-line settings.
"""

import argparse
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

DEFAULT_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
CONNECTION_FIELDS = {
    "compartment": ("COMPARTMENT", None),
    "config-file": ("OCI_CONFIG_FILE", "~/.oci/config"),
    "profile": ("OCI_PROFILE", "DEFAULT"),
    "region": ("REGION", "eu-frankfurt-1"),
    "instance-id": ("AIDP_INSTANCE_ID", None),
    "workspace-name": ("WORKSPACE_NAME", None),
    "endpoint": ("AIDP_ENDPOINT", None),
}


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


def connection_parser(argv, description, default_env_file=DEFAULT_ENV_FILE):
    """Create a parser and dotenv lookup, preserving CLI > environment > file order.

    Args:
        argv: CLI arguments, or None for process arguments.
        description: Feature help text.
        default_env_file: Default settings path, independent of the working directory.

    Returns:
        Parser, settings lookup callable, and selected dotenv path.
    """
    bootstrap = argparse.ArgumentParser(add_help=False)
    bootstrap.add_argument("--env-file", default=str(default_env_file))
    initial, _ = bootstrap.parse_known_args(argv)
    env_path = Path(initial.env_file).expanduser()
    parser = argparse.ArgumentParser(parents=[bootstrap], description=description)
    if (
        initial.env_file != str(default_env_file)
        and not env_path.is_file()
        and not any(arg in ("-h", "--help") for arg in (argv or []))
    ):
        parser.error("The explicitly selected .env file does not exist.")
    try:
        values = {**dotenv_values(env_path, interpolate=False), **os.environ}
    except OSError:
        parser.error("Cannot read the selected .env file.")

    def setting(name, default=None):
        value = values.get(name)
        return value.strip() if value and value.strip() else default

    for flag, (variable, default) in CONNECTION_FIELDS.items():
        parser.add_argument(
            f"--{flag}",
            default=setting(variable, default),
            help=f"Override {variable} from .env.",
        )
    return parser, setting, env_path


def validate_connection(args, parser):
    """Validate connection settings without loading credentials or making requests."""
    for name in ("compartment", "profile", "config_file", "region"):
        if not getattr(args, name) or not getattr(args, name).strip():
            parser.error(
                f"Set --{name.replace('_', '-')} or its corresponding .env variable."
            )
    if not re.fullmatch(r"[a-z]+(?:-[a-z0-9]+)+-\d+", args.region):
        parser.error("REGION must be an OCI region identifier.")
    try:
        args.endpoint = endpoint_origin(args.endpoint) if args.endpoint else None
    except (argparse.ArgumentTypeError, ValueError) as exc:
        parser.error(str(exc))
