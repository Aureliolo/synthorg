"""Tools namespace setting definitions.

Covers git subprocess kill-grace, Atlas migration subprocess
kill-grace, Docker sandbox sidecar resource limits, Docker stop
grace period, and subprocess sandbox kill-grace.
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

# ── Git / Atlas subprocess kill-grace ────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="git_kill_grace_timeout_seconds",
        type=SettingType.FLOAT,
        default="5.0",
        description=(
            "Grace period after SIGTERM for a git subprocess to flush"
            " before it is reaped"
        ),
        group="Git",
        level=SettingLevel.ADVANCED,
        min_value=1.0,
        max_value=60.0,
        yaml_path="tools.git.kill_grace_timeout_seconds",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="atlas_kill_grace_timeout_seconds",
        type=SettingType.FLOAT,
        default="5.0",
        description=(
            "Grace period after SIGTERM for an Atlas migration subprocess"
            " to flush before it is reaped"
        ),
        group="Atlas",
        level=SettingLevel.ADVANCED,
        min_value=1.0,
        max_value=60.0,
        yaml_path="tools.atlas.kill_grace_timeout_seconds",
    )
)

# ── Docker sandbox sidecar ───────────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="docker_sidecar_health_poll_interval_seconds",
        type=SettingType.FLOAT,
        default="0.2",
        description="Interval between sidecar container health probes",
        group="Docker Sandbox",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=0.05,
        max_value=5.0,
        yaml_path="tools.docker.sidecar_health_poll_interval_seconds",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="docker_sidecar_health_timeout_seconds",
        type=SettingType.FLOAT,
        default="15.0",
        description=(
            "Maximum time to wait for the sidecar container to report"
            " healthy before failing sandbox startup"
        ),
        group="Docker Sandbox",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=1.0,
        max_value=300.0,
        yaml_path="tools.docker.sidecar_health_timeout_seconds",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="docker_sidecar_memory_limit",
        type=SettingType.STRING,
        default="64m",
        description=(
            "Memory limit for the sandbox sidecar container, as a Docker"
            " size string. Accepts raw bytes (e.g. '1048576') or a"
            " single-character unit suffix 'b'/'k'/'m'/'g' (case-insensitive):"
            " '512b', '64k', '64m', '1G'. The leading digit must be non-zero."
        ),
        group="Docker Sandbox",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        validator_pattern=r"^[1-9]\d*[bkmgBKMG]?$",
        yaml_path="tools.docker.sidecar_memory_limit",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="docker_sidecar_cpu_limit",
        type=SettingType.FLOAT,
        default="0.5",
        description="CPU quota (in cores) for the sandbox sidecar container",
        group="Docker Sandbox",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=0.1,
        max_value=16.0,
        yaml_path="tools.docker.sidecar_cpu_limit",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="docker_sidecar_max_pids",
        type=SettingType.INTEGER,
        default="32",
        description=(
            "Maximum number of processes allowed inside the sidecar"
            " container (PIDs cgroup limit)"
        ),
        group="Docker Sandbox",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=1,
        max_value=4096,
        yaml_path="tools.docker.sidecar_max_pids",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="docker_stop_grace_timeout_seconds",
        type=SettingType.INTEGER,
        default="5",
        description=(
            "Grace period Docker waits after SIGTERM before sending SIGKILL"
            " to sandbox containers"
        ),
        group="Docker Sandbox",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=1,
        max_value=300,
        yaml_path="tools.docker.stop_grace_timeout_seconds",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="subprocess_kill_grace_timeout_seconds",
        type=SettingType.FLOAT,
        default="5.0",
        description=(
            "Grace period after SIGTERM for a subprocess-sandbox child to"
            " flush before it is reaped"
        ),
        group="Subprocess Sandbox",
        level=SettingLevel.ADVANCED,
        min_value=1.0,
        max_value=60.0,
        yaml_path="tools.subprocess.kill_grace_timeout_seconds",
    )
)

# ── Web tool HTTP request timeout ────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="web_request_timeout_seconds",
        type=SettingType.FLOAT,
        default="30.0",
        description=(
            "Maximum wall-clock time a web-tool HTTP request may run before"
            " it is cancelled."
        ),
        group="Web Tools",
        level=SettingLevel.ADVANCED,
        min_value=5.0,
        max_value=300.0,
        yaml_path="tools.web_request_timeout_seconds",
    )
)

# ── Git command timeout (overall execution bound) ────────────────
# Distinct from ``git_kill_grace_timeout_seconds`` (post-SIGTERM grace);
# this caps total git subprocess wall-clock.

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="git_command_timeout_seconds",
        type=SettingType.FLOAT,
        default="60.0",
        description=(
            "Maximum wall-clock time a git subprocess invocation (clone,"
            " fetch, commit, etc.) may run before it is cancelled."
        ),
        group="Git",
        level=SettingLevel.ADVANCED,
        min_value=10.0,
        max_value=3600.0,
        yaml_path="tools.git_command_timeout_seconds",
    )
)
