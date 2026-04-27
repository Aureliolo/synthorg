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
        yaml_path="communication.bus_bridge.poll_timeout_seconds",
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
        yaml_path="communication.bus_bridge.max_consecutive_errors",
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
        yaml_path="communication.bus_bridge.drain_timeout_seconds",
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
        yaml_path="communication.webhook_bridge.poll_timeout_seconds",
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
        yaml_path="communication.webhook_bridge.max_consecutive_errors",
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
        yaml_path="communication.nats.history_batch_size",
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
        yaml_path="communication.nats.history_fetch_timeout_seconds",
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
        yaml_path="communication.delegation.record_store_max_size",
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
        yaml_path="communication.event_stream.max_queue_size",
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
        yaml_path="communication.loop_prevention.window_seconds",
    )
)

# ── Meeting protocol token reserves ──────────────────────────────
# Three protocols each reserve a fraction of the meeting token budget
# for their final synthesis/summary phase. Surfaced for operator
# visibility; applied at protocol construction so changes take effect
# on next restart.

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COMMUNICATION,
        key="roundrobin_summary_reserve_fraction",
        type=SettingType.FLOAT,
        default="0.20",
        description=(
            "Fraction of token budget reserved for the summary phase in"
            " round-robin meetings (default 20%)"
        ),
        group="Meetings",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=0.0,
        max_value=1.0,
        yaml_path=(
            "communication.meeting_protocol.round_robin.summary_reserve_fraction"
        ),
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COMMUNICATION,
        key="positionpapers_synthesis_reserve_fraction",
        type=SettingType.FLOAT,
        default="0.20",
        description=(
            "Fraction of token budget reserved for synthesis in"
            " position-papers meetings (default 20%)"
        ),
        group="Meetings",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=0.0,
        max_value=1.0,
        yaml_path=(
            "communication.meeting_protocol.position_papers.synthesis_reserve_fraction"
        ),
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COMMUNICATION,
        key="structuredphases_synthesis_reserve_fraction",
        type=SettingType.FLOAT,
        default="0.20",
        description=(
            "Fraction of remaining token budget reserved for synthesis in"
            " structured-phases meetings (default 20%)"
        ),
        group="Meetings",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=0.0,
        max_value=1.0,
        yaml_path=(
            "communication.meeting_protocol.structured_phases.synthesis_reserve_fraction"
        ),
    )
)

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
        yaml_path="communication.meetings.enabled",
    )
)

# ── Escalation queue + sweeper (CFG-1 audit) ─────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COMMUNICATION,
        key="escalation_default_result_limit",
        type=SettingType.INTEGER,
        default="50",
        description=(
            "Default row limit when querying the in-memory escalation"
            " queue. Overridable per-call."
        ),
        group="Escalation",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=1,
        max_value=1000,
        yaml_path="communication.escalation_default_result_limit",
    )
)

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
        yaml_path="communication.escalation_sweeper_paused",
    )
)
