"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: MCP connection-settings loading and error adaptation.
"""

import argparse
import os

from aidp_common.connection import AidpError
from aidp_common.settings import connection_parser, validate_connection
from aidp_mcp.local_files import allowed_local_roots


class McpArgumentParser(argparse.ArgumentParser):
    """Convert MCP configuration errors into actionable tool failures."""

    def error(self, message):
        """Raise an error that FastMCP returns without ending its process.

        Args:
            message: Sanitized argparse validation message.

        Raises:
            AidpError: Always, with the original actionable message.
        """
        raise AidpError(message)


def load_connection_settings():
    """Load and validate the shared project connection settings.

    Returns:
        argparse.Namespace: Validated shared connection settings.

    Raises:
        AidpError: The project configuration is incomplete or invalid.
    """
    parser, setting, env_path = connection_parser(
        [],
        "Run AI DP notebook workflow MCP tools.",
        parser_class=McpArgumentParser,
    )
    args = parser.parse_args([])
    missing_required = any(
        not getattr(args, name, None)
        for name in ("compartment", "profile", "config_file", "region")
    )
    if (
        missing_required
        and not env_path.is_file()
        and not os.environ.get("AIDP_ENV_FILE")
    ):
        parser.error(
            "Configuration is unavailable: create the root .env file or set "
            "AIDP_ENV_FILE to an absolute settings-file path."
        )
    validate_connection(args, parser)
    if not args.workspace_name:
        parser.error("Set WORKSPACE_NAME before using AI DP MCP tools.")
    args.allowed_roots = allowed_local_roots(setting("AIDP_ALLOWED_ROOTS", ""))
    return args
