"""Cockpit namespace setting definitions.

Knobs for the mission-control cockpit: flight-recorder capture and
retention, live-activity stuck/runaway heuristics, and the pluggable
recorder-sink and steering-directive strategies. Consumers live in
``src/synthorg/engine/flight_recording/``, ``src/synthorg/engine/cockpit/``,
``src/synthorg/engine/intervention/`` and ``api/controllers/cockpit.py``.
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COCKPIT,
        key="flight_recorder_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Capture a flight-recorder frame after each agent turn for"
            " step-by-step cockpit replay. Disabling stops new frames"
            " being recorded; existing frames remain queryable."
        ),
        group="Flight Recorder",
        level=SettingLevel.BASIC,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COCKPIT,
        key="flight_recorder_retention_days",
        type=SettingType.INTEGER,
        default="90",
        description=(
            "Retain flight-recorder frames for this many days; the daily"
            " purge loop removes frames older than the cut-off."
        ),
        group="Flight Recorder",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=3650,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COCKPIT,
        key="flight_recorder_summary_max_chars",
        type=SettingType.INTEGER,
        default="2000",
        description=(
            "Maximum length of the redacted prompt/response summaries"
            " stored on each frame; longer content is truncated."
        ),
        group="Flight Recorder",
        level=SettingLevel.ADVANCED,
        min_value=100,
        max_value=20000,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COCKPIT,
        key="flight_recorder_sink_strategy",
        type=SettingType.ENUM,
        default="persistence",
        enum_values=("persistence", "noop"),
        description=(
            "Recorder sink implementation selected at boot. 'persistence'"
            " appends frames to the connected backend; 'noop' discards"
            " them (frames are not recorded)."
        ),
        group="Flight Recorder",
        level=SettingLevel.ADVANCED,
        read_only_post_init=True,
        restart_required=True,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COCKPIT,
        key="stuck_idle_threshold_minutes",
        type=SettingType.FLOAT,
        default="10.0",
        description=(
            "An in-progress or blocked agent idle for longer than this is"
            " flagged as stuck in the live activity snapshot."
        ),
        group="Live Activity",
        level=SettingLevel.BASIC,
        min_value=1.0,
        max_value=1440.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COCKPIT,
        key="runaway_cost_threshold_percent",
        type=SettingType.FLOAT,
        default="150.0",
        description=(
            "An agent whose accumulated cost exceeds this percentage of"
            " the approved forecast ceiling is flagged as runaway."
        ),
        group="Live Activity",
        level=SettingLevel.BASIC,
        min_value=100.0,
        max_value=1000.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COCKPIT,
        key="snapshot_interval_seconds",
        type=SettingType.FLOAT,
        default="5.0",
        description=(
            "Cadence at which the cockpit publishes a live activity"
            " snapshot delta on the WebSocket cockpit channel."
        ),
        group="Live Activity",
        level=SettingLevel.ADVANCED,
        min_value=1.0,
        max_value=300.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COCKPIT,
        key="steering_directive_strategy",
        type=SettingType.ENUM,
        default="safe_default",
        enum_values=("safe_default",),
        description=(
            "Steering directive implementation selected at boot. The"
            " 'safe_default' strategy applies hint and redirect"
            " interventions as an INFO_REQUEST interrupt at the next safe"
            " turn boundary."
        ),
        group="Intervention",
        level=SettingLevel.ADVANCED,
        read_only_post_init=True,
        restart_required=True,
    )
)
