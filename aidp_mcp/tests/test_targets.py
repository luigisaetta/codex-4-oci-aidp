"""
Author: L. Saetta
Date last modified: 2026-09-27
License: MIT
Description: Offline tests for MCP target discovery and target caching.
"""

from concurrent.futures import ThreadPoolExecutor
import time
from types import SimpleNamespace
from unittest.mock import Mock

import oci
import pytest

from aidp_common.connection import AidpError
from aidp_mcp import targets


@pytest.fixture(autouse=True)
def clear_process_target_cache():
    """Keep target-resolution cache state out of unrelated tests."""
    targets.clear_target_cache()
    yield
    targets.clear_target_cache()


def _target_settings(**overrides):
    """Return complete target-selection settings for resolution tests."""
    values = {
        "config_file": "~/.oci/config",
        "profile": "DEFAULT",
        "region": "eu-frankfurt-1",
        "compartment": "ocid1.compartment.example",
        "instance_id": "ocid1.aidp.example",
        "workspace_name": "workspace",
        "endpoint": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _resolve_cached_target(settings, monkeypatch, *, need_workspace):
    """Resolve through the cache with discovery replaced by offline mocks."""
    discover = Mock(return_value=[SimpleNamespace(id="instance")])
    workspace = Mock(return_value="workspace-key")
    monkeypatch.setattr(targets, "_discover_instances", discover)
    monkeypatch.setattr(targets, "find_workspace", workspace)
    managed = Mock(return_value=Mock())
    monkeypatch.setattr(targets, "managed_client", managed)
    result = getattr(targets, "_resolve_target")(
        settings,
        {"tenancy": "tenancy"},
        {},
        {},
        Mock(),
        need_workspace=need_workspace,
    )
    return result, discover, workspace, managed


def test_target_resolution_reuses_complete_workspace_target(monkeypatch):
    """An identical second workspace resolution performs no discovery or setup."""
    settings = _target_settings()
    first, discover, workspace, managed = _resolve_cached_target(
        settings, monkeypatch, need_workspace=True
    )
    second = getattr(targets, "_resolve_target")(
        settings,
        {"tenancy": "tenancy"},
        {},
        {},
        Mock(),
        need_workspace=True,
    )

    assert first[0] == targets.ResolvedTarget("instance", "workspace-key")
    assert first[2] is False
    assert second[0] == first[0]
    assert second[2] is True
    discover.assert_called_once()
    workspace.assert_called_once()
    assert managed.call_count == 3


def test_catalog_then_workspace_reuses_cached_instance(monkeypatch):
    """Adding a workspace key does not rediscover the selected instance."""
    settings = _target_settings()
    _, discover, workspace, _ = _resolve_cached_target(
        settings, monkeypatch, need_workspace=False
    )
    getattr(targets, "_resolve_target")(
        settings,
        {"tenancy": "tenancy"},
        {},
        {},
        Mock(),
        need_workspace=True,
    )

    discover.assert_called_once()
    workspace.assert_called_once()


def test_target_resolution_uses_new_key_when_workspace_changes(monkeypatch):
    """A target setting change cannot reuse identifiers from another target."""
    settings = _target_settings()
    _, discover, _, _ = _resolve_cached_target(
        settings, monkeypatch, need_workspace=False
    )
    getattr(targets, "_resolve_target")(
        _target_settings(workspace_name="other-workspace"),
        {"tenancy": "tenancy"},
        {},
        {},
        Mock(),
        need_workspace=False,
    )

    assert discover.call_count == 2


def test_target_resolution_concurrent_misses_discover_once(monkeypatch):
    """The process lock coalesces two simultaneous first resolutions."""
    settings = _target_settings()

    def discover(*_args):
        time.sleep(0.05)
        return [SimpleNamespace(id="instance")]

    mocked_discover = Mock(side_effect=discover)
    monkeypatch.setattr(targets, "_discover_instances", mocked_discover)
    monkeypatch.setattr(targets, "managed_client", Mock(return_value=Mock()))

    def resolve():
        return getattr(targets, "_resolve_target")(
            settings,
            {"tenancy": "tenancy"},
            {},
            {},
            Mock(),
            need_workspace=False,
        )[0]

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _unused: resolve(), range(2)))

    assert results == [
        targets.ResolvedTarget("instance"),
        targets.ResolvedTarget("instance"),
    ]
    mocked_discover.assert_called_once()


def test_target_resolution_failure_does_not_populate_cache(monkeypatch):
    """A failed discovery cannot leave a partial target for a later call."""
    settings = _target_settings()
    monkeypatch.setattr(
        targets, "_discover_instances", Mock(side_effect=AidpError("no"))
    )
    monkeypatch.setattr(targets, "managed_client", Mock(return_value=Mock()))

    with pytest.raises(AidpError, match="no"):
        getattr(targets, "_resolve_target")(
            settings,
            {"tenancy": "tenancy"},
            {},
            {},
            Mock(),
            need_workspace=False,
        )

    assert not getattr(targets, "_TARGET_CACHE")


def test_cached_target_is_cleared_after_tool_404(monkeypatch):
    """A 404 from tool work invalidates the complete cached target once."""
    settings = _target_settings()
    cache_key = ("target",)
    getattr(targets, "_TARGET_CACHE")[cache_key] = targets.ResolvedTarget(
        "instance", "workspace-key"
    )
    monkeypatch.setattr(
        targets,
        "load_auth",
        Mock(return_value=({"tenancy": "tenancy"}, {})),
    )
    monkeypatch.setattr(
        targets,
        "_resolve_target",
        Mock(
            return_value=(
                targets.ResolvedTarget("instance", "workspace-key"),
                cache_key,
                True,
            )
        ),
    )
    monkeypatch.setattr(targets, "managed_client", Mock(return_value=Mock()))

    with pytest.raises(oci.exceptions.ServiceError):
        with targets.workspace_clients(settings):
            raise oci.exceptions.ServiceError(404, "NotFound", {}, "gone")

    assert cache_key not in getattr(targets, "_TARGET_CACHE")


def test_agent_clients_use_the_workspace_target_and_preserve_timestamps(monkeypatch):
    """Agent observations share target caching and safe Workbench client options."""
    settings = _target_settings()
    managed = Mock(return_value=Mock())
    monkeypatch.setattr(
        targets, "load_auth", Mock(return_value=({"tenancy": "tenancy"}, {}))
    )
    monkeypatch.setattr(
        targets,
        "_resolve_target",
        Mock(return_value=(targets.ResolvedTarget("instance", "workspace"), (), False)),
    )
    monkeypatch.setattr(targets, "managed_client", managed)

    with targets.agent_clients(settings) as clients:
        assert clients[:2] == ("instance", "workspace")

    assert managed.call_args.kwargs["preserve_timestamps"] is True


def test_workspace_clients_include_a_dedicated_workspace_object_client(monkeypatch):
    """Workspace object operations use a configured client separate from notebooks."""
    settings = _target_settings()
    managed = Mock(return_value=Mock())
    monkeypatch.setattr(
        targets, "load_auth", Mock(return_value=({"tenancy": "tenancy"}, {}))
    )
    monkeypatch.setattr(
        targets,
        "_resolve_target",
        Mock(return_value=(targets.ResolvedTarget("instance", "workspace"), (), False)),
    )
    monkeypatch.setattr(targets, "managed_client", managed)

    with targets.workspace_clients(settings) as clients:
        assert clients[:2] == ("instance", "workspace")
        assert len(clients) == 6

    assert managed.call_args_list[-1].args[1] is targets.WorkspaceObjectClient
    assert managed.call_args_list[-1].kwargs["preserve_timestamps"] is True


def test_named_compartment_uses_instance_compartment_lookup():
    """An explicit instance avoids tenancy-wide compartment enumeration."""
    identity = Mock()
    control = Mock()
    instance = SimpleNamespace(
        id="instance", compartment_id="compartment-id", lifecycle_state="ACTIVE"
    )
    control.get_ai_data_platform.return_value.data = instance
    identity.get_compartment.return_value.data = SimpleNamespace(
        name="development", lifecycle_state="ACTIVE"
    )

    result = getattr(targets, "_discover_instances")(
        identity,
        control,
        {"tenancy": "tenancy"},
        _target_settings(compartment="development"),
    )

    assert result == [instance]
    control.get_ai_data_platform.assert_called_once_with("ocid1.aidp.example")
    identity.get_compartment.assert_called_once_with("compartment-id")
