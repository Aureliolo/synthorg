# module-kind: declarative
"""Tools namespace setting definitions.

Covers git subprocess kill-grace, Docker sandbox sidecar resource
limits, Docker stop grace period, subprocess sandbox kill-grace,
native web search, MCP stdio-server sandboxing, and the forge / chat
agent tools.
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
        key="docker_connect_timeout_seconds",
        type=SettingType.FLOAT,
        default="30.0",
        description=(
            "Ceiling on connecting to the Docker daemon and resolving this"
            " process's own workspace storage. Bounds the connect path only,"
            " never a running command. A cold Docker Desktop daemon can"
            " outlast the default, which leaves every sandbox-backed tool"
            " unavailable; raise it there. Resolved per connect, so a change"
            " applies without a restart."
        ),
        group="Docker Sandbox",
        level=SettingLevel.ADVANCED,
        min_value=1.0,
        max_value=600.0,
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
        default="",
        enum_values=("brave", "tavily", "exa", "ollama"),
        description=(
            "Which search provider backs the web_search tool. Ships unset, so"
            " enabling web search never bills a vendor nobody chose: pick one"
            " and bind a connection holding its key. The options differ in the"
            " index they search, whether results come back answer-shaped or as"
            " ranked links, whether ranking is semantic or keyword, which"
            " recency and domain filters they can express, and how they price"
            " a request. Each reads its key from the bound web_search"
            " connection."
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
            " API key (read from its 'api_key', 'token', or 'access_token'"
            " credential field). Empty disables web search even when enabled."
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

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="web_search_notice_dismissed",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Whether the dashboard stops raising an enabled-but-unconfigured"
            " web search with the operator. A deployment content with"
            " local-only page reading is not misconfigured, so the notice can"
            " be dismissed; it returns if the configuration changes."
        ),
        group="Web Search",
        level=SettingLevel.ADVANCED,
    )
)

# ── Page fetching ────────────────────────────────────────────────
# The local rung needs no credential and no spend, so the tool ships ON:
# an agent can already reach the same pages through http_request, and this
# reads them as markdown instead of raw DOM. The rungs that cost something
# (a vendor's reader, a container) are opt-in on top.

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="web_fetch_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Whether agents may read a web page as markdown. On by default:"
            " the local backend needs no API key and grants no reach the"
            " existing http_request tool does not already have, while"
            " returning far less noise per page."
        ),
        group="Web Fetch",
        level=SettingLevel.BASIC,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="web_fetch_proxy_enabled",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Whether the 'proxy' backend is offered, which hands the target"
            " URL to the configured search vendor's own reader. Off by"
            " default: it spends against the bound connection. Needs"
            " web_search_connection bound and web_search_provider set to a"
            " vendor that ships a reader, which not every search vendor does;"
            " the rung stays absent, with a log line naming the reason, when"
            " the selected one sells search only."
        ),
        group="Web Fetch",
        level=SettingLevel.BASIC,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="web_fetch_render_enabled",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Whether the 'render' backend is offered, which drives the"
            " headless browser so a page that builds its body in JavaScript"
            " becomes readable. Off by default: it needs the Docker sandbox"
            " and costs a container start per fetch."
        ),
        group="Web Fetch",
        level=SettingLevel.BASIC,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="web_fetch_max_characters",
        type=SettingType.INTEGER,
        default="40000",
        description=(
            "Ceiling on the markdown a single fetch returns. Content past it"
            " is cut at a paragraph boundary and the result says so, so the"
            " agent knows to narrow rather than assuming it read the page."
        ),
        group="Web Fetch",
        level=SettingLevel.ADVANCED,
        min_value=1000,
        max_value=500000,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="web_fetch_max_response_bytes",
        type=SettingType.INTEGER,
        default="2097152",
        description=(
            "Hard ceiling on the response body the local backend reads from"
            " the wire, before extraction. Bounds memory on a hostile or"
            " misconfigured target."
        ),
        group="Web Fetch",
        level=SettingLevel.ADVANCED,
        min_value=65536,
        max_value=52428800,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="web_fetch_user_agent",
        type=SettingType.STRING,
        default="SynthOrgBot/1.0 (+https://synthorg.io/bot)",
        description=(
            "User-Agent the local backend sends. Servers vary what they"
            " return by it, so it is operator-visible rather than hidden;"
            " identifying the fetcher honestly is also what lets a site"
            " rate-limit us rather than block the whole address."
        ),
        group="Web Fetch",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="web_fetch_docs_index_discovery_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Whether a successful fetch also probes the site for an"
            " '/llms.txt' documentation index and reports it. Costs one small"
            " request and often replaces several page fetches. The probe is"
            " made by this process directly, even when the fetch itself went"
            " through a vendor's reader, so turn it off if this process should"
            " not contact the sites being read."
        ),
        group="Web Fetch",
        level=SettingLevel.ADVANCED,
    )
)

# ── Chat inbound (Slack Socket-Mode) ─────────────────────────────
# Off by default: an inbound control surface (a human reply resumes a
# parked task) must be opted into explicitly. The resident consumer loop
# reads the kill-switch live per iteration and connects only when a bound
# connection with a Socket-Mode app-level token is also configured.

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="chat_inbound_enabled",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Whether inbound Slack Socket-Mode is active. Off by default: when"
            " on, a human mention / DM / reply / reaction in an approval thread"
            " resumes the parked task it answers. Needs chat_inbound_connection"
            " set to a Slack connection holding an app-level token."
        ),
        group="Chat Inbound",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="chat_inbound_connection",
        type=SettingType.STRING,
        default="",
        description=(
            "Name of the Slack connection whose app-level token (its"
            " 'app_token' credential) opens the inbound Socket-Mode socket."
            " Empty keeps inbound inert even when chat_inbound_enabled is on."
        ),
        group="Chat Inbound",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="chat_inbound_deciders",
        type=SettingType.STRING,
        default="",
        description=(
            "Comma-separated Slack user IDs allowed to decide an approval from"
            " a chat thread. Empty denies every inbound decision: reacting in a"
            " channel is not authorisation on its own, so an operator names the"
            " deciders explicitly. A reaction from anyone else is ignored."
        ),
        group="Chat Inbound",
        level=SettingLevel.ADVANCED,
    )
)

# ── MCP server sandboxing ────────────────────────────────────────
# A stdio MCP server is arbitrary third-party code; per ADR D16 every
# execution-capable surface runs in a container. Enabled by default:
# disabling re-exposes host execution and should only happen where Docker
# is unavailable. Applies on the next MCP bridge rebuild (no restart).
# The image is not one of these knobs: the MCP runtime is the resolved
# ``tools.sandbox_image``, so the image an operator hardened and the image
# untrusted MCP code runs in cannot become two different answers.

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
        description=(
            "Docker --cpus quota (in cores) for an MCP server container."
            " Must be greater than zero: the daemon reads a quota of zero as"
            " 'no limit', so it uncaps the container rather than clamping it."
        ),
        group="MCP",
        level=SettingLevel.ADVANCED,
        # Rejects every spelling of zero ("0", "0.0", "00"), which the plain
        # digit pattern admitted and the daemon turns into an unlimited
        # container rather than a refusal.
        validator_pattern=r"^(?!0+(\.0+)?$)\d+(\.\d+)?$",
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
            " running the backend outside the CLI. The container was"
            " created against the resolved image, so this is fixed for the"
            " life of that container."
        ),
        group="Docker Sandbox",
        level=SettingLevel.ADVANCED,
        compose_set=True,
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
            " default. The container was created against the resolved"
            " image, so this is fixed for the life of that container."
        ),
        group="Docker Sandbox",
        level=SettingLevel.ADVANCED,
        compose_set=True,
        env_var_override="SYNTHORG_SIDECAR_IMAGE",
    )
)

# ── Shell command timeout (overall execution bound) ──────────────
# The ceiling on ONE agent shell command. It was a code default of 30s that no
# operator surface exposed, and 30s is less than a dependency install takes: a
# live run watched an agent time out on `npm install` four times in a row,
# write its tests anyway, and fail them for want of the packages. A command
# that cannot finish is not a slow command, it is a capability the deployment
# does not have, so the number that decides it belongs where an operator can
# see and change it.

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="shell_command_timeout_seconds",
        type=SettingType.FLOAT,
        default="120.0",
        description=(
            "Maximum wall-clock time one agent shell command may run before"
            " it is cancelled, when the call does not name its own timeout."
            " Covers ordinary dependency installs and build steps; an agent"
            " may still ask for longer per call, up to ten minutes. Raising"
            " it lets slower work finish and lets a hung command hold its"
            " sandbox slot for longer."
        ),
        group="Terminal",
        level=SettingLevel.ADVANCED,
        min_value=10.0,
        max_value=600.0,
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
        key="browser_content_max_characters",
        type=SettingType.INTEGER,
        default="40000",
        description=(
            "Ceiling on the readable content the browser tool's content mode"
            " returns. The mode extracts markdown from the rendered page rather"
            " than handing back the raw document, because a script-heavy page"
            " serialises to megabytes of markup around the part worth reading."
            " A page over the ceiling is cut at a block boundary and says so."
        ),
        group="Browser",
        level=SettingLevel.ADVANCED,
        min_value=1000,
        max_value=500000,
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
            " to launch headless. Applies to browser sessions started"
            " after the runtime rebuild the change triggers."
        ),
        group="Browser",
        level=SettingLevel.ADVANCED,
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
            " observation channel. Applies to desktop sessions started"
            " after the runtime rebuild the change triggers."
        ),
        group="Desktop",
        level=SettingLevel.ADVANCED,
        validator_pattern=r"^(xvfb|vnc)$",
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
            " agent's applications require. Applies to desktop sessions"
            " started after the runtime rebuild the change triggers."
        ),
        group="Desktop",
        level=SettingLevel.ADVANCED,
    )
)

# ── Forge agent tools ────────────────────────────────────────────
# The forge tools (read repo/file, open/comment issues, open/comment/
# review/merge PRs, read CI) are ghost-wired at boot: built only when
# enabled AND a bound forge connection is set, so a misconfigured feature
# never crashes the runtime. Writes route through the identity-bound
# approval flow. A settings change applies on the next runtime rebuild
# (no process restart).

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="forge_tools_enabled",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Whether agents may drive a forge (GitHub / Forgejo) through the"
            " forge_repo, forge_issue, forge_pull_request, and forge_ci tools."
            " Off by default: a connection holding a forge access token must"
            " be bound first. Writes always require approval."
        ),
        group="Forge Tools",
        level=SettingLevel.BASIC,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="forge_tools_connection",
        type=SettingType.STRING,
        default="",
        description=(
            "Name of the forge connection (GitHub / Forgejo) holding the"
            " access token (read from its 'token' credential field) and the"
            " repository host base_url. Empty disables the forge tools even"
            " when enabled."
        ),
        group="Forge Tools",
        level=SettingLevel.BASIC,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="forge_tools_timeout_seconds",
        type=SettingType.FLOAT,
        default="30.0",
        description=(
            "Maximum wall-clock time a single forge API request may run"
            " before it is cancelled."
        ),
        group="Forge Tools",
        level=SettingLevel.ADVANCED,
        min_value=5.0,
        max_value=300.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="forge_tools_max_read_chars",
        type=SettingType.INTEGER,
        default="100000",
        description=(
            "Maximum characters a single forge file read returns to the agent"
            " before the content is truncated (keeps a large file from"
            " flooding the context window)."
        ),
        group="Forge Tools",
        level=SettingLevel.ADVANCED,
        min_value=1000,
        max_value=1000000,
    )
)

# ── Chat agent tools ─────────────────────────────────────────────
# The chat tools (send/read messages, read threads, list channels, look
# up users) are ghost-wired at boot: built only when enabled AND a bound
# chat connection is set. Sending a message routes through the
# identity-bound approval flow. A settings change applies on the next
# runtime rebuild (no process restart).

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="chat_tools_enabled",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Whether agents may use the operator chat channel through the"
            " chat_messages and chat_directory tools. Off by default: a"
            " connection holding a chat bot token must be bound first."
            " Sending a message always requires approval."
        ),
        group="Chat Tools",
        level=SettingLevel.BASIC,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="chat_tools_connection",
        type=SettingType.STRING,
        default="",
        description=(
            "Name of the chat connection (Slack) holding the bot token (read"
            " from its 'token' credential field). Empty disables the chat"
            " tools even when enabled. The same connection also backs the"
            " Slack notification sink."
        ),
        group="Chat Tools",
        level=SettingLevel.BASIC,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="chat_tools_timeout_seconds",
        type=SettingType.FLOAT,
        default="30.0",
        description=(
            "Maximum wall-clock time a single chat API request may run before"
            " it is cancelled."
        ),
        group="Chat Tools",
        level=SettingLevel.ADVANCED,
        min_value=5.0,
        max_value=300.0,
    )
)

# ── Deploy tools ─────────────────────────────────────────────────
# The governed deploy tools release to, and observe, an external hosting
# platform. Unlike forge / chat there is no single bound connection: an
# organisation deploys to several targets, so a call names one and the
# host checks it against the allowlist below before brokering any
# credential. Triggering a release always parks a human approval.

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="deploy_tools_enabled",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Whether agents may trigger and observe deployments through the"
            " deploy_release and deploy_run tools. Off by default: a deploy"
            " target must be created and allowlisted first. Enabling exposes"
            " a destructive, externally-reaching capability, so the enable"
            " transition takes the deliberate confirm+reason+actor guardrail."
        ),
        group="Deploy Tools",
        level=SettingLevel.BASIC,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="deploy_tools_targets",
        type=SettingType.STRING,
        default="",
        description=(
            "Comma-separated names of deploy connections agents may target"
            " (e.g. 'staging-web,production-web'). Empty allows nothing"
            " (secure default). A call naming a target outside this list is"
            " refused before any credential is read. Each target's"
            " environment is set on the connection, not by the agent, so"
            " adding a production target here widens real blast radius:"
            " widening takes the confirm+reason+actor guardrail."
        ),
        group="Deploy Tools",
        level=SettingLevel.BASIC,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="deploy_tools_timeout_seconds",
        type=SettingType.FLOAT,
        default="30.0",
        description=(
            "Maximum wall-clock time a single deploy API request may run"
            " before it is cancelled."
        ),
        group="Deploy Tools",
        level=SettingLevel.ADVANCED,
        min_value=5.0,
        max_value=300.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="deploy_tools_max_log_chars",
        type=SettingType.INTEGER,
        default="20000",
        description=(
            "Maximum characters of deployment log an agent may pull in one"
            " call. Build logs routinely echo environment detail, so this"
            " bounds how much of it can reach a prompt at once; the tool"
            " reports when it truncated."
        ),
        group="Deploy Tools",
        level=SettingLevel.ADVANCED,
        min_value=1000,
        max_value=200000,
    )
)

# ── Publish tools ────────────────────────────────────────────────
# The governed publish tools push, and inspect, container images on an
# external registry. Like deploy there is no single bound connection: an
# organisation publishes to several registries, so a call names one and the
# host checks it against the allowlist below before brokering any credential.
# Pushing an image always parks a human approval.

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="publish_tools_enabled",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Whether agents may publish and inspect container images through"
            " the publish_push and publish_inspect tools. Off by default: a"
            " registry target must be created and allowlisted first. Enabling"
            " exposes a destructive, externally-reaching capability, so the"
            " enable transition takes the deliberate confirm+reason+actor"
            " guardrail."
        ),
        group="Publish Tools",
        level=SettingLevel.BASIC,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="publish_tools_targets",
        type=SettingType.STRING,
        default="",
        description=(
            "Comma-separated names of registry connections agents may target"
            " (e.g. 'staging-images,production-images'). Empty allows nothing"
            " (secure default). A call naming a target outside this list is"
            " refused before any credential is read. Each target's release"
            " channel is set on the connection, not by the agent, so adding a"
            " production target here widens real blast radius: widening takes"
            " the confirm+reason+actor guardrail."
        ),
        group="Publish Tools",
        level=SettingLevel.BASIC,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="publish_tools_timeout_seconds",
        type=SettingType.FLOAT,
        default="60.0",
        description=(
            "Maximum wall-clock time a single registry API request may run"
            " before it is cancelled. Higher than the deploy default because"
            " a blob upload moves more than a status read."
        ),
        group="Publish Tools",
        level=SettingLevel.ADVANCED,
        min_value=5.0,
        max_value=600.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="publish_tools_max_manifest_bytes",
        type=SettingType.INTEGER,
        default="4000000",
        description=(
            "Maximum size of a single image manifest the tools read or"
            " publish in one call. Bounds a promote and each manifest a"
            " workspace push uploads."
        ),
        group="Publish Tools",
        level=SettingLevel.ADVANCED,
        min_value=1000,
        max_value=16000000,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.TOOLS,
        key="publish_tools_max_image_bytes",
        type=SettingType.INTEGER,
        default="2000000000",
        description=(
            "Maximum total bytes a single workspace push may upload (config +"
            " layers + manifests). Bounds how large an image an agent can push"
            " host-side in one call."
        ),
        group="Publish Tools",
        level=SettingLevel.ADVANCED,
        min_value=1000000,
        max_value=10000000000,
    )
)
