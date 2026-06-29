"""Process-singleton cache for resolved sidecar / stop-grace tool limits.

Decouples the operator-tunable Docker sidecar resource limits and the sandbox
stop-grace timeout (canonically owned by ``ConfigResolver`` via the registered
``tools.docker_sidecar_*`` / ``tools.docker_stop_grace_timeout_seconds``
settings) from the per-container-launch read sites in
:mod:`synthorg.tools.sandbox.docker_sandbox_sidecar` and
:mod:`synthorg.tools.sandbox.docker_sandbox_lifecycle`, which previously used
hardcoded module constants.

Unlike the sibling image-resolution cache (``tools.{sandbox,sidecar}_image``
are ``read_only_post_init`` and seeded once), these settings are hot-reloadable:
``_apply_tools_bridge_config_snapshot`` seeds the cache at startup and
``ToolsBridgeSettingsSubscriber`` re-seeds it on every operator change, so the
next container launch reads the new value with no restart.

The cache holds a single :class:`ToolsBridgeConfig` snapshot; the getters read
one field each, falling back to ``ToolsBridgeConfig()`` defaults (the single
source of truth for the documented constants) when the cache is unset
(programmatic instantiation outside lifecycle / test fixtures). GIL-atomic
single-reference read/write; test fixtures restore via
``set_resolved_sidecar_limits(None)`` on teardown.
"""

from synthorg.settings.bridge_configs import ToolsBridgeConfig

_FALLBACK: ToolsBridgeConfig = ToolsBridgeConfig()

_resolved: ToolsBridgeConfig | None = None


def set_resolved_sidecar_limits(config: ToolsBridgeConfig | None) -> None:
    """Replace the cached sidecar/stop-grace snapshot; ``None`` clears it.

    Called at startup by ``_apply_tools_bridge_config_snapshot`` and on each
    operator change by ``ToolsBridgeSettingsSubscriber``. Tests use the same
    setter (with ``None`` on teardown) to override / restore the cache.
    """
    global _resolved  # noqa: PLW0603 -- module-level cache
    _resolved = config


def _current() -> ToolsBridgeConfig:
    """Return the cached snapshot, falling back to bridge defaults."""
    return _resolved if _resolved is not None else _FALLBACK


def get_resolved_sidecar_memory_limit() -> str:
    """Return the resolved sidecar memory limit (Docker size string)."""
    return _current().docker_sidecar_memory_limit


def get_resolved_sidecar_cpu_limit() -> float:
    """Return the resolved sidecar CPU quota in cores."""
    return _current().docker_sidecar_cpu_limit


def get_resolved_sidecar_max_pids() -> int:
    """Return the resolved sidecar PIDs cgroup limit."""
    return _current().docker_sidecar_max_pids


def get_resolved_sidecar_health_poll_interval_seconds() -> float:
    """Return the resolved sidecar health-probe poll interval (seconds)."""
    return _current().docker_sidecar_health_poll_interval_seconds


def get_resolved_sidecar_health_timeout_seconds() -> float:
    """Return the resolved sidecar health-probe timeout (seconds)."""
    return _current().docker_sidecar_health_timeout_seconds


def get_resolved_docker_stop_grace_timeout_seconds() -> int:
    """Return the resolved sandbox-container stop-grace timeout (seconds)."""
    return _current().docker_stop_grace_timeout_seconds
