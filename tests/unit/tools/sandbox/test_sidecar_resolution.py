"""Tests for the per-launch sidecar / stop-grace resolution cache.

The Docker sandbox sidecar + lifecycle code reads these getters per container
launch / stop, and ``ToolsBridgeSettingsSubscriber`` re-seeds the cache on an
operator change, so a ``tools.docker_sidecar_*`` edit applies without a restart.
"""

import pytest

from synthorg.settings.bridge_configs import ToolsBridgeConfig
from synthorg.tools.sandbox._sidecar_resolution import (
    get_resolved_docker_stop_grace_timeout_seconds,
    get_resolved_sidecar_cpu_limit,
    get_resolved_sidecar_health_poll_interval_seconds,
    get_resolved_sidecar_health_timeout_seconds,
    get_resolved_sidecar_max_pids,
    get_resolved_sidecar_memory_limit,
    set_resolved_sidecar_limits,
)

pytestmark = pytest.mark.unit


def test_getters_fall_back_to_bridge_defaults() -> None:
    """An unset cache returns the ``ToolsBridgeConfig`` field defaults."""
    set_resolved_sidecar_limits(None)
    defaults = ToolsBridgeConfig()
    assert get_resolved_sidecar_memory_limit() == defaults.docker_sidecar_memory_limit
    assert get_resolved_sidecar_cpu_limit() == defaults.docker_sidecar_cpu_limit
    assert get_resolved_sidecar_max_pids() == defaults.docker_sidecar_max_pids
    assert (
        get_resolved_sidecar_health_poll_interval_seconds()
        == defaults.docker_sidecar_health_poll_interval_seconds
    )
    assert (
        get_resolved_sidecar_health_timeout_seconds()
        == defaults.docker_sidecar_health_timeout_seconds
    )
    assert (
        get_resolved_docker_stop_grace_timeout_seconds()
        == defaults.docker_stop_grace_timeout_seconds
    )


def test_seeded_snapshot_is_read_per_getter() -> None:
    """A seeded snapshot's operator values are returned by each getter."""
    set_resolved_sidecar_limits(
        ToolsBridgeConfig(
            docker_sidecar_memory_limit="128m",
            docker_sidecar_cpu_limit=1.5,
            docker_sidecar_max_pids=64,
            docker_sidecar_health_poll_interval_seconds=0.5,
            docker_sidecar_health_timeout_seconds=30.0,
            docker_stop_grace_timeout_seconds=10,
        )
    )
    try:
        assert get_resolved_sidecar_memory_limit() == "128m"
        assert get_resolved_sidecar_cpu_limit() == 1.5
        assert get_resolved_sidecar_max_pids() == 64
        assert get_resolved_sidecar_health_poll_interval_seconds() == 0.5
        assert get_resolved_sidecar_health_timeout_seconds() == 30.0
        assert get_resolved_docker_stop_grace_timeout_seconds() == 10
    finally:
        # Reset the process singleton explicitly so isolation does not depend
        # solely on the directory-scoped autouse conftest fixture.
        set_resolved_sidecar_limits(None)
