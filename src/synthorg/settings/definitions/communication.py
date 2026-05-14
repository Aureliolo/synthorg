"""Communication namespace setting definitions.

Covers bus/NATS transport, event stream, delegation record store,
loop prevention, and bus bridges for API and engine workflow.
"""

from synthorg.observability import get_logger
from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

logger = get_logger(__name__)

_r = get_registry()

# ── Bus bridges (API + workflow webhook) ─────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COMMUNICATION,
        key="bus_bridge_poll_timeout_seconds",
        type=SettingType.FLOAT,
        default="1.0",
        description="Poll timeout for the API bus bridge loop",
        group="Bus Bridge",
        level=SettingLevel.ADVANCED,
        min_value=0.1,
        max_value=10.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COMMUNICATION,
        key="bus_bridge_max_consecutive_errors",
        type=SettingType.INTEGER,
        default="30",
        description=("Maximum consecutive errors before the API bus bridge aborts"),
        group="Bus Bridge",
        level=SettingLevel.ADVANCED,
        min_value=5,
        max_value=100,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COMMUNICATION,
        key="bus_bridge_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Live kill-switch for the API bus bridge per-channel polling"
            " loop. When ``False`` each iteration short-circuits before"
            " consuming a bus message; the polling task stays resident so"
            " operators can re-enable without restarting the API. Resolved"
            " per iteration so changes take effect on the next poll."
        ),
        group="Bus Bridge",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COMMUNICATION,
        key="bus_bridge_drain_timeout_seconds",
        type=SettingType.FLOAT,
        default="10.0",
        description=(
            "Hard deadline for the bus bridge stop() drain. The drain"
            " is wrapped in asyncio.wait_for so the lifecycle lock"
            " cannot be held indefinitely if a polling task ignores"
            " cancellation. Resolved at stop() entry."
        ),
        group="Bus Bridge",
        level=SettingLevel.ADVANCED,
        min_value=1.0,
        max_value=300.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COMMUNICATION,
        key="nats_url",
        type=SettingType.STRING,
        default="nats://nats:4222",
        description=(
            "[Bootstrap-only -- read via RootConfig at startup; this entry"
            " exists for /settings discoverability only.] NATS server URL."
            " Sourced from SYNTHORG_NATS_URL env > YAML"
            " (communication.nats.url) > default. The bus driver opens"
            " its connection once at boot, so a runtime change requires"
            " a process restart."
        ),
        group="NATS",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        read_only_post_init=True,
        # Override the auto-derived ``SYNTHORG_COMMUNICATION_NATS_URL``
        # with the established operator-facing name ``SYNTHORG_NATS_URL``.
        # The Docker-compose template, the local-dev shell setup, and
        # external operator runbooks already use this short form;
        # honouring it here preserves the existing operator surface
        # while still routing the value through the registry.
        env_var_override="SYNTHORG_NATS_URL",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COMMUNICATION,
        key="webhook_bridge_poll_timeout_seconds",
        type=SettingType.FLOAT,
        default="1.0",
        description="Poll timeout for the engine workflow webhook bridge",
        group="Bus Bridge",
        level=SettingLevel.ADVANCED,
        min_value=0.1,
        max_value=10.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COMMUNICATION,
        key="webhook_bridge_max_consecutive_errors",
        type=SettingType.INTEGER,
        default="30",
        description=("Maximum consecutive errors before the webhook bridge aborts"),
        group="Bus Bridge",
        level=SettingLevel.ADVANCED,
        min_value=5,
        max_value=100,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COMMUNICATION,
        key="webhook_bridge_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Live kill-switch for the engine workflow webhook bridge poll"
            " loop. When False the loop stays resident but every iteration"
            " short-circuits -- pauses event forwarding without tearing"
            " down lifecycle. Resolver outage falls back to enabled."
        ),
        group="Bus Bridge",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COMMUNICATION,
        key="escalation_notify_subscriber_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Live kill-switch for the Postgres escalation notify"
            " subscriber loop. When False the loop stays resident but"
            " every reconnect attempt short-circuits -- the local"
            " sweeper + per-resolver timeouts cover eventual consistency"
            " while the subscriber is paused. Resolver outage falls back"
            " to enabled."
        ),
        group="Escalation",
        level=SettingLevel.ADVANCED,
    )
)

# ── NATS transport ───────────────────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COMMUNICATION,
        key="nats_history_batch_size",
        type=SettingType.INTEGER,
        default="100",
        description=("Message batch size for NATS JetStream history replay fetch"),
        group="NATS",
        level=SettingLevel.ADVANCED,
        min_value=10,
        max_value=1000,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COMMUNICATION,
        key="nats_history_fetch_timeout_seconds",
        type=SettingType.FLOAT,
        default="0.5",
        description="Per-batch fetch timeout for NATS history replay",
        group="NATS",
        level=SettingLevel.ADVANCED,
        min_value=0.1,
        max_value=5.0,
    )
)

# ── Delegation + event stream + loop prevention ──────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COMMUNICATION,
        key="delegation_record_store_max_size",
        type=SettingType.INTEGER,
        default="10000",
        description=(
            "Maximum delegation records retained in the in-memory store before"
            " FIFO eviction. NOTE: DelegationRecordStore is constructed by the"
            " caller of create_app (not inside create_app itself), so this"
            " setting is surfaced for completeness but is not yet threaded into"
            " the default construction path. Wiring is tracked as follow-up on"
            " #1398/#1400; until then a change requires rebuilding the store"
            " with the desired max_records and restarting the process."
        ),
        group="Delegation",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=100,
        max_value=1_000_000,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COMMUNICATION,
        key="event_stream_max_queue_size",
        type=SettingType.INTEGER,
        default="256",
        description=(
            "Maximum events buffered per subscriber queue before backpressure"
            " kicks in. NOTE: EventStreamHub is constructed inside create_app"
            " before the ConfigResolver is available, and asyncio.Queue is"
            " created at subscribe time with a fixed maxsize -- changing the"
            " value on an existing hub would only affect new subscribers."
            " Runtime wiring is tracked as follow-up on #1398/#1400; until then"
            " a change requires a process restart with the default overridden"
            " at EventStreamHub construction."
        ),
        group="Event Stream",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=16,
        max_value=10000,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COMMUNICATION,
        key="event_stream_subscriber_idle_ttl_seconds",
        type=SettingType.FLOAT,
        default="86400.0",
        description=(
            "Inactivity TTL for SSE subscribers on the EventStreamHub."
            " Subscribers whose queue has not received an event within"
            " this window are pruned by the janitor. Default 24h matches"
            " the long-lived-SSE-client expectation: a dashboard tab"
            " can stay subscribed across a quiet workday without being"
            " evicted, while crashed/disconnected sessions still get"
            " reclaimed within the day. Resolved once at lifespan"
            " startup; runtime changes require a restart."
        ),
        group="Event Stream",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=30.0,
        max_value=86400.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COMMUNICATION,
        key="event_stream_janitor_interval_seconds",
        type=SettingType.FLOAT,
        default="300.0",
        description=(
            "Wall-clock interval between EventStreamHub janitor sweeps."
            " Default 5min balances memory-reclaim latency against"
            " wakeup overhead under low subscriber churn. Resolved once"
            " at lifespan startup; runtime changes require a restart."
        ),
        group="Event Stream",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=5.0,
        max_value=3600.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COMMUNICATION,
        key="loop_prevention_window_seconds",
        type=SettingType.FLOAT,
        default="60.0",
        description=(
            "Window over which repeated inter-agent messages are tracked"
            " for loop detection"
        ),
        group="Loop Prevention",
        level=SettingLevel.ADVANCED,
        min_value=5.0,
        max_value=600.0,
    )
)

# ── Meeting protocol token reserves ──────────────────────────────
# Three protocols each reserve a fraction of the meeting token budget
# for their final synthesis/summary phase. Surfaced for operator
# visibility; applied at protocol construction so changes take effect
# on next restart.

# ── Kill switches (CFG-1 audit) ──────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COMMUNICATION,
        key="meetings_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Organization-level kill switch for the meetings subsystem."
            " Disable to pause all scheduled and event-triggered"
            " meetings without removing meeting types from config."
        ),
        group="Meetings",
    )
)

# ── Escalation queue + sweeper (CFG-1 audit) ─────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COMMUNICATION,
        key="escalation_sweeper_paused",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Pause flag for the escalation expiration sweeper. When"
            " True the sweeper stays resident but every tick"
            " short-circuits -- used for debugging stuck escalations."
        ),
        group="Escalation",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COMMUNICATION,
        key="escalation_subscriber_reconnect_delay_seconds",
        type=SettingType.FLOAT,
        default="1.0",
        description=(
            "[Bootstrap-only -- read via EscalationQueueConfig at startup;"
            " this entry exists for /settings discoverability only.]"
            " Delay before the Postgres LISTEN/NOTIFY escalation"
            " subscriber retries after a connection drop."
        ),
        group="Escalation",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        read_only_post_init=True,
        min_value=0.1,
        max_value=60.0,
    )
)
