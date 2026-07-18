"""Tools namespace setting definitions.

Covers git subprocess kill-grace, Docker sandbox sidecar resource
limits, Docker stop grace period, and subprocess sandbox kill-grace.
"""

from synthorg import __version__
from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

# ── Git subprocess kill-grace ────────────────────────────────────

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
    )
)

# ── Docker sandbox sidecar ───────────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="docker_sidecar_health_poll_interval_seconds",
        type=SettingType.FLOAT,
        default="0.2",
        description=(
            "Interval between sidecar container health probes. Resolved"
            " per container launch, so a change applies without a restart."
        ),
        group="Docker Sandbox",
        level=SettingLevel.ADVANCED,
        min_value=0.05,
        max_value=5.0,
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
            " healthy before failing sandbox startup. Resolved per"
            " container launch, so a change applies without a restart."
        ),
        group="Docker Sandbox",
        level=SettingLevel.ADVANCED,
        min_value=1.0,
        max_value=300.0,
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
            " Resolved per container launch, so a change applies without"
            " a restart."
        ),
        group="Docker Sandbox",
        level=SettingLevel.ADVANCED,
        validator_pattern=r"^[1-9]\d*[bkmgBKMG]?$",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="docker_sidecar_cpu_limit",
        type=SettingType.FLOAT,
        default="0.5",
        description=(
            "CPU quota (in cores) for the sandbox sidecar container."
            " Resolved per container launch, so a change applies without"
            " a restart."
        ),
        group="Docker Sandbox",
        level=SettingLevel.ADVANCED,
        min_value=0.1,
        max_value=16.0,
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
            " container (PIDs cgroup limit). Resolved per container"
            " launch, so a change applies without a restart."
        ),
        group="Docker Sandbox",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=4096,
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
            " to sandbox containers. Resolved per container stop, so a"
            " change applies without a restart."
        ),
        group="Docker Sandbox",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=300,
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
    )
)

# ── Native web search ────────────────────────────────────────────
# The provider is ghost-wired at boot: built only when enabled AND a
# bound connection resolves an API key, so a misconfigured feature never
# crashes the runtime. A settings change applies on the next runtime
# rebuild (no process restart). ``enum_values`` mirror
# ``tools.web.providers.presets.SEARCH_PROVIDER_IDS`` (asserted equal by a
# unit test) rather than importing the preset registry into the settings
# bootstrap.

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="web_search_enabled",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Whether agents may search the web. Off by default: a provider"
            " and a connection holding its API key must be configured first."
            " When on, the web_search tool is granted to the agent runtime and"
            " the research subsystem's web source."
        ),
        group="Web Search",
        level=SettingLevel.BASIC,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="web_search_provider",
        type=SettingType.ENUM,
        default="brave",
        enum_values=("brave", "tavily", "exa"),
        description=(
            "Which search provider backs the web_search tool. 'brave' (default)"
            " is the broadest independent index; 'tavily' returns answer-shaped"
            " results; 'exa' does semantic/conceptual search. All three read"
            " their API key from the bound web_search connection."
        ),
        group="Web Search",
        level=SettingLevel.BASIC,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="web_search_connection",
        type=SettingType.STRING,
        default="",
        description=(
            "Name of the generic_http connection holding the search provider's"
            " API key (in its 'api_key' credential field). Empty disables web"
            " search even when enabled."
        ),
        group="Web Search",
        level=SettingLevel.BASIC,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="web_search_max_results",
        type=SettingType.INTEGER,
        default="10",
        description=(
            "Default maximum results a single web search returns. Clamped down"
            " to the selected provider's own ceiling."
        ),
        group="Web Search",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=100,
    )
)

# ── MCP server sandboxing ────────────────────────────────────────
# A stdio MCP server is arbitrary third-party code; per ADR D16 every
# execution-capable surface runs in a container. Enabled by default:
# disabling re-exposes host execution and should only happen where Docker
# is unavailable. Applies on the next MCP bridge rebuild (no restart).

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="mcp_sandbox_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Run stdio MCP servers inside a hardened Docker container"
            " (cap-drop, no-new-privileges, read-only rootfs, resource"
            " limits). Disabling re-exposes host execution; only do so where"
            " Docker is unavailable."
        ),
        group="MCP",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="mcp_sandbox_image",
        type=SettingType.STRING,
        default="node:22-alpine",
        description=(
            "Container image used to run stdio MCP servers. Must provide"
            " Node/npx so ``npx -y <package>`` can launch the server."
        ),
        group="MCP",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="mcp_sandbox_memory_limit",
        type=SettingType.STRING,
        default="512m",
        description=(
            "Docker --memory limit for an MCP server container (Docker size"
            " string, e.g. '512m', '1g')."
        ),
        group="MCP",
        level=SettingLevel.ADVANCED,
        validator_pattern=r"^[1-9]\d*[bkmgBKMG]?$",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="mcp_sandbox_pids_limit",
        type=SettingType.INTEGER,
        default="256",
        description=(
            "Maximum number of processes inside an MCP server container"
            " (PIDs cgroup limit)."
        ),
        group="MCP",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=4096,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="mcp_sandbox_cpus",
        type=SettingType.STRING,
        default="1.0",
        description="Docker --cpus quota (in cores) for an MCP server container.",
        group="MCP",
        level=SettingLevel.ADVANCED,
        validator_pattern=r"^\d+(\.\d+)?$",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="mcp_sandbox_network",
        type=SettingType.STRING,
        default="bridge",
        description=(
            "Docker --network mode for an MCP server container. MCP servers"
            " reach external APIs, so 'bridge' by default; 'none' blocks all"
            " egress (only for servers that need no network)."
        ),
        group="MCP",
        level=SettingLevel.ADVANCED,
        validator_pattern=r"^(bridge|none|host)$",
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
    )
)

# ── Sandbox image references (env-var-aware bootstrap) ───────────
# Backed by the resolver's DB > env > YAML > default chain so the
# canonical resolved value lives at the settings layer rather than
# being re-read from ``os.environ`` inside Pydantic field defaults.
# ``env_var_override`` matches the historical env vars the CLI
# injects into the backend container, preserving operator workflow.

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="sandbox_image",
        type=SettingType.STRING,
        default=f"ghcr.io/aureliolo/synthorg-sandbox:v{__version__}",
        description=(
            "Docker image used for sandbox containers. Resolution"
            " precedence at backend startup: DB override >"
            " ``SYNTHORG_SANDBOX_IMAGE`` env var > YAML"
            " ``tools.sandbox.docker.image`` > registered code default."
            " The CLI injects a digest-pinned reference via the env var,"
            " so DB / YAML overrides are mostly relevant for operators"
            " running the backend outside the CLI. Resolved once at"
            " startup and injected into ``DockerSandboxConfig`` via the"
            " sandbox image-resolution cache; ``read_only_post_init``"
            " keeps later DB writes from drifting from the resolved"
            " value used at boot."
        ),
        group="Docker Sandbox",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        read_only_post_init=True,
        env_var_override="SYNTHORG_SANDBOX_IMAGE",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="sidecar_image",
        type=SettingType.STRING,
        default=f"ghcr.io/aureliolo/synthorg-sidecar:v{__version__}",
        description=(
            "Docker image used for the sandbox network sidecar"
            " container. Resolution precedence at backend startup: DB"
            " override > ``SYNTHORG_SIDECAR_IMAGE`` env var > YAML"
            " ``tools.sandbox.docker.sidecar_image`` > registered code"
            " default. Resolved once at startup and injected into"
            " ``DockerSandboxConfig`` via the sidecar image-resolution"
            " cache; ``read_only_post_init`` keeps later DB writes from"
            " drifting from the resolved value used at boot, and"
            " ``restart_required`` is set because changes only take"
            " effect after the backend container restarts."
        ),
        group="Docker Sandbox",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        read_only_post_init=True,
        env_var_override="SYNTHORG_SIDECAR_IMAGE",
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
    )
)

# ── Git log result cap ───────────────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="git_log_max_count",
        type=SettingType.INTEGER,
        default="100",
        description=(
            "Upper bound on the number of commits the git_log tool"
            " returns; a per-call max_count above this is clamped down."
        ),
        group="Git",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=10_000,
    )
)

# ── Code runner output tail cap ──────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="code_runner_output_tail_limit",
        type=SettingType.INTEGER,
        default="2000",
        description=(
            "Maximum characters of captured stdout/stderr the code_runner"
            " tool keeps on a test-execution record."
        ),
        group="Code Execution",
        level=SettingLevel.ADVANCED,
        min_value=100,
        max_value=1_000_000,
    )
)

# ── Headless browser tool (Playwright) ───────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="browser_launch_timeout_seconds",
        type=SettingType.FLOAT,
        default="30.0",
        description=(
            "Maximum wall-clock time the headless browser tool may wait"
            " for Chromium to launch inside the sandbox before failing."
        ),
        group="Browser",
        level=SettingLevel.ADVANCED,
        min_value=5.0,
        max_value=300.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="browser_viewport_width",
        type=SettingType.INTEGER,
        default="1280",
        description="Default viewport width (pixels) for browser screenshots.",
        group="Browser",
        level=SettingLevel.BASIC,
        min_value=320,
        max_value=4096,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="browser_viewport_height",
        type=SettingType.INTEGER,
        default="720",
        description="Default viewport height (pixels) for browser screenshots.",
        group="Browser",
        level=SettingLevel.BASIC,
        min_value=320,
        max_value=4096,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="browser_screenshot_ssim_tolerance",
        type=SettingType.FLOAT,
        default="0.98",
        description=(
            "Default SSIM pass threshold for screenshot diffs. Per-call"
            " tolerance overrides this when set on the tool arguments."
        ),
        group="Browser",
        level=SettingLevel.ADVANCED,
        min_value=0.5,
        max_value=1.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="browser_a11y_min_impact_default",
        type=SettingType.STRING,
        default="serious",
        description=(
            "Minimum axe-core impact level treated as a violation when"
            " the tool call omits min_impact. One of: minor, moderate,"
            " serious, critical."
        ),
        group="Browser",
        level=SettingLevel.BASIC,
        validator_pattern=r"^(minor|moderate|serious|critical)$",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="browser_image_pin",
        type=SettingType.STRING,
        default="mcr.microsoft.com/playwright/python:v1.61.0-jammy",
        description=(
            "Container image used by the browser sandbox backend. Must"
            " contain Python 3, Playwright Python, and Chromium ready"
            " to launch headless."
        ),
        group="Browser",
        level=SettingLevel.ADVANCED,
        restart_required=True,
    )
)

# ── Virtual desktop tool (Xvfb + xdotool + scrot) ────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="desktop_driver",
        type=SettingType.STRING,
        default="xvfb",
        description=(
            "Virtual desktop driver strategy. 'xvfb' is the deterministic"
            " headless default; 'vnc' additionally exposes an x11vnc"
            " observation channel."
        ),
        group="Desktop",
        level=SettingLevel.ADVANCED,
        validator_pattern=r"^(xvfb|vnc)$",
        restart_required=True,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="desktop_screen_width",
        type=SettingType.INTEGER,
        default="1280",
        description="Virtual screen width (pixels) for the desktop session.",
        group="Desktop",
        level=SettingLevel.BASIC,
        min_value=320,
        max_value=4096,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="desktop_screen_height",
        type=SettingType.INTEGER,
        default="800",
        description="Virtual screen height (pixels) for the desktop session.",
        group="Desktop",
        level=SettingLevel.BASIC,
        min_value=320,
        max_value=4096,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="desktop_image_pin",
        type=SettingType.STRING,
        default=f"ghcr.io/aureliolo/synthorg-desktop:v{__version__}",
        description=(
            "Container image used by the desktop sandbox backend. Must"
            " contain Xvfb, xdotool, scrot, and the GUI toolkits the"
            " agent's applications require."
        ),
        group="Desktop",
        level=SettingLevel.ADVANCED,
        restart_required=True,
    )
)
