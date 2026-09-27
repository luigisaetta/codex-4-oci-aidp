"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Explicit plan-and-confirm guards for MCP mutations.
"""

from aidp_common.connection import AidpError


def require_confirmation(confirmed, message):
    """Require the explicit boolean confirmation for a remote mutation.

    MCP operations are read-only by default. Validate inputs before creating
    remote clients, and never make remote writes while reporting a plan.

    Args:
        confirmed: The caller-provided confirmation flag.
        message: Existing actionable error message for omitted confirmation.

    Raises:
        AidpError: The confirmation is not exactly ``True``.
    """
    if confirmed is not True:
        raise AidpError(message)


def should_apply(apply, action):
    """Return whether a planned mutation should be submitted.

    Args:
        apply: The caller-provided plan/apply flag.
        action: The resolved plan action.

    Returns:
        bool: Whether a non-no-op mutation is explicitly requested.
    """
    return apply is True and action != "unchanged"
