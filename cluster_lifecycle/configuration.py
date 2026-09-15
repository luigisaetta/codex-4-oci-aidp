"""
Author: L. Saetta
Date last modified: 2026-09-15
License: MIT
Description: Load lifecycle settings and validate optional Workbench endpoint overrides.
"""

import argparse
from pathlib import Path

from aidp_common.settings import connection_parser, validate_connection

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
    parser, setting, _ = connection_parser(
        argv, "Inspect, start or stop an OCI AI DP cluster.", DEFAULT_ENV_FILE
    )
    parser.add_argument(
        "action",
        nargs="?",
        choices=("start", "stop", "status"),
        default=setting("ACTION", "status"),
    )
    fields = {
        "cluster-name": ("CLUSTER_NAME", None),
        "workspace-key": ("WORKSPACE_KEY", None),
        "cluster-type": ("CLUSTER_TYPE", None),
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
        validate_connection(args, parser)
        if not args.cluster_name or not args.cluster_name.strip():
            parser.error("Set CLUSTER_NAME in .env or pass --cluster-name.")
        if args.action not in ("start", "stop", "status"):
            parser.error("ACTION must be start, stop or status.")
        if args.cluster_type not in (None, "USER", "AI_COMPUTE"):
            parser.error("CLUSTER_TYPE must be USER or AI_COMPUTE.")
        if args.workspace_key and args.workspace_name:
            parser.error("Use either WORKSPACE_KEY or WORKSPACE_NAME, not both.")
    except (argparse.ArgumentTypeError, ValueError) as exc:
        parser.error(str(exc))
    return args
