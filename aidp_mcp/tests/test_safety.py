"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Offline tests for explicit MCP mutation safety guards.
"""

import pytest

from aidp_common.connection import AidpError
from aidp_mcp.safety import require_confirmation, should_apply


@pytest.mark.parametrize("value", [False, None, "true", 1])
def test_require_confirmation_rejects_everything_except_true(value):
    """Only the exact boolean true may authorize a remote mutation."""
    with pytest.raises(AidpError, match="confirm"):
        require_confirmation(value, "Set confirm=true to submit the mutation.")


def test_require_confirmation_accepts_true():
    """The explicit boolean true preserves a valid mutation request."""
    require_confirmation(True, "unused")


@pytest.mark.parametrize(
    ("apply", "action", "expected"),
    [(False, "create", False), (True, "unchanged", False), (True, "update", True)],
)
def test_should_apply_requires_a_requested_non_noop_mutation(apply, action, expected):
    """Plan mode and a no-op never perform remote writes."""
    assert should_apply(apply, action) is expected
