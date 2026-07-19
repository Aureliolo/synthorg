# module-kind: declarative
"""Chief-of-Staff namespace setting definitions.

Each Chief-of-Staff capability flag and per-feature model is an
individual runtime setting so the wizard and dashboard Settings can
toggle it over the standard ``/settings`` API. Every capability here is
live: the conversational ones (explain-chat, propose, concern-routing,
group-chat) are gated per request/turn, and the autonomous ones (learning,
alerts, narrative, invite) are gated per cycle/turn or started/stopped by a
settings subscriber, so toggling any of them takes effect with no restart.
The autonomous capabilities additionally require the persona master switch
``self_improvement.chief_of_staff_enabled``. The per-feature models are read
live per LLM call. ``direct_mcp_enabled`` (letting a chat instruction drive a
real MCP action) is fail-closed: a settings subscriber rebuilds the actor
through the startup governance gate on change, so it too toggles with no
restart while still materialising only when security governance is wired.

Values overlay onto :class:`~synthorg.meta.chief_of_staff.config.ChiefOfStaffConfig`
at load time (see :func:`synthorg.meta.config.load_self_improvement_config`);
they are the single source of truth for these flags and models.
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()
_NS = SettingNamespace.CHIEF_OF_STAFF

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="explain_chat_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Let you ask the Chief of Staff to explain proposals, alerts,"
            " and signals in plain language (the /meta/chat explain path)."
        ),
        group="Conversational",
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="chat_snapshot_window_days",
        type=SettingType.INTEGER,
        default="7",
        description=(
            "How many trailing days of org signals the Chief of Staff"
            " considers when answering a chat question (the /meta/chat"
            " explain path). Resolved fresh per request; a change applies"
            " without a restart."
        ),
        group="Conversational",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=90,
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="chat_org_state_max_items_per_section",
        type=SettingType.INTEGER,
        default="10",
        description=(
            "How many records the Chief of Staff lists per org-state section"
            " (in-progress tasks, in-review tasks, active projects, pending"
            " approvals) when answering a chat question. The full count is"
            " always reported; only the sample is bounded. Resolved fresh per"
            " request; a change applies without a restart."
        ),
        group="Conversational",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=100,
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="propose_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Let the Chief of Staff clarify a request and draft it into a plan"
            " for your review when the unified chat classifies a turn as work."
        ),
        group="Conversational",
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="routing_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Route each conversational turn to the most-senior relevant role"
            " agent instead of always answering as the generic persona. Gated"
            " live per turn in the proposer; also requires the Chief-of-Staff"
            " persona master switch."
        ),
        group="Conversational",
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="group_chat_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Hold a multi-agent group conversation with several role agents"
            " in one room when the unified chat convenes a group."
        ),
        group="Conversational",
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="turn_router_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Let the unified chat classify each message and dispatch it to the"
            " right capability (answer, propose work, act, convene a group, or"
            " draft a charter) so you talk to your org in one conversation"
            " instead of picking a mode (the /meta/chat/turn path). Each"
            " capability still enforces its own toggle. Gated live per request."
        ),
        group="Conversational",
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="multi_voice_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Let specialists (your CFO, CTO, and other roles) add a short,"
            " attributed perspective to an answer when their role genuinely"
            " adds a distinct angle, so you see the organisation answering"
            " rather than one voice. On by default; stays quiet on simple"
            " questions. Turn off for single-voice answers only. Gated live"
            " per request."
        ),
        group="Conversational",
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="learning_enabled",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Track proposal approval/rejection patterns and adjust future"
            " proposal confidence on its own. Spends and changes scoring in"
            " the background; off by default. Re-read live each cycle; also"
            " requires the Chief-of-Staff persona master switch."
        ),
        group="Automation",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="alerts_enabled",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Run the proactive org-inflection alerts daemon, which checks for"
            " signal inflections on a timer and spends to do so. Off by"
            " default. A settings subscriber starts/stops the daemon live;"
            " also requires the Chief-of-Staff persona master switch."
        ),
        group="Automation",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="narrative_enabled",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Generate a per-run narrative (documentary mode) after each"
            " completed brief. Spends an extra LLM call per run; off by"
            " default. Gated live per run; also requires the Chief-of-Staff"
            " persona master switch."
        ),
        group="Automation",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="invite_enabled",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Let agents request to pull other agents into a group chat on"
            " their own (gated by your consent). Off by default: agents act"
            " on your behalf only when you opt in. Gated live per group-chat"
            " turn; also requires the Chief-of-Staff persona master switch."
        ),
        group="Acts on your behalf",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="direct_mcp_enabled",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Let a chat instruction drive a real MCP action under the acting"
            " agent's trust level when the unified chat classifies a turn as an"
            " action. Off by default: the Chief of Staff acts for you only when"
            " you opt in. Fail-closed:"
            " it materialises only when security governance and the MCP"
            " self-consumer are wired, and stays inert (503) otherwise. A live"
            " toggle rebuilds the actor through that same fail-closed gate, so"
            " it takes effect with no restart."
        ),
        group="Acts on your behalf",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="operator_console_enabled",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Let the unified chat operate the control plane itself when it"
            " classifies a turn as a configure request: connect an integration,"
            " change a setting, install a catalogue entry, call any control-plane"
            " tool. Acts as a shared system console identity, not one of your"
            " agents. Off by default. Fail-closed: it materialises only when"
            " security governance and the MCP self-consumer are wired and a"
            " console model is selected, and stays inert (503) otherwise. A live"
            " toggle rebuilds the console through that same fail-closed gate, so"
            " it takes effect with no restart. Independent of the direct-MCP"
            " acting toggle."
        ),
        group="Acts on your behalf",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="operator_console_max_turns",
        type=SettingType.INTEGER,
        default="12",
        description=(
            "Hard turn cap for one operator-console session (bounds the"
            " configure/observe fan-out a single instruction can drive). Applied"
            " on the next console rebuild (any chief-of-staff settings write)."
        ),
        group="Acts on your behalf",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=30,
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="operator_console_cost_ceiling_usd",
        type=SettingType.FLOAT,
        default="1.0",
        description=(
            "Hard per-session cost ceiling (USD) for the operator console; the"
            " session halts the moment accumulated cost crosses it, independent"
            " of the turn cap. Applied on the next console rebuild."
        ),
        group="Acts on your behalf",
        level=SettingLevel.ADVANCED,
        min_value=0.01,
        max_value=100.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="operator_console_autonomy_level",
        type=SettingType.ENUM,
        default="semi",
        description=(
            "Autonomy tier the operator console acts under. 'semi' (default)"
            " lets reads flow while risky writes escalate to the approval inbox;"
            " 'supervised' escalates more; 'locked' parks nearly everything;"
            " 'full' runs everything the hard-deny floor still permits. Applied"
            " on the next console rebuild."
        ),
        group="Acts on your behalf",
        level=SettingLevel.ADVANCED,
        enum_values=("full", "semi", "supervised", "locked"),
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="chat_model",
        type=SettingType.MODEL_REF,
        default="",
        description=(
            "Provider + model for Chief-of-Staff chat responses, selected"
            " through the model picker (a `{provider, model_id}` reference)."
            " Empty keeps the built-in default until setup auto-selects one"
            " from your provider catalogue. Read live per call."
        ),
        group="Models",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="propose_model",
        type=SettingType.MODEL_REF,
        default="",
        description=(
            "Provider + model for the clarify-and-propose turns, selected"
            " through the model picker (a `{provider, model_id}` reference)."
            " Empty keeps the built-in default. Read live per call."
        ),
        group="Models",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="multi_voice_model",
        type=SettingType.MODEL_REF,
        default="",
        description=(
            "Provider + model for the multi-voice chime-ins, selected through"
            " the model picker (a `{provider, model_id}` reference). Empty"
            " leaves chime-ins off until a model is set. Read live per call."
        ),
        group="Models",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="routing_model",
        type=SettingType.MODEL_REF,
        default="",
        description=(
            "Provider + model for the concern-routing classifier, selected"
            " through the model picker (a `{provider, model_id}` reference)."
            " Empty keeps the built-in default. Read live per call."
        ),
        group="Models",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="turn_intent_model",
        type=SettingType.MODEL_REF,
        default="",
        description=(
            "Provider + model for the unified turn-intent classifier, selected"
            " through the model picker (a `{provider, model_id}` reference)."
            " Empty leaves the unified router without a classifier, so every"
            " turn is answered as a plain question. Read live per call."
        ),
        group="Models",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="operator_console_model",
        type=SettingType.MODEL_REF,
        default="",
        description=(
            "Provider + model for the operator-console configure loop, selected"
            " through the model picker (a `{provider, model_id}` reference)."
            " Empty keeps the console fail-closed (a configure turn 503s) until a"
            " model is selected. Applied on the next console rebuild."
        ),
        group="Models",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="configure_intent_confidence_floor",
        type=SettingType.FLOAT,
        default="0.85",
        description=(
            "Minimum classifier confidence before a turn may resolve to"
            " CONFIGURE (operator console); below it the turn degrades to a plain"
            " answer. Read live per turn, so raising this safety floor takes"
            " effect without a restart."
        ),
        group="Chat",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
        max_value=1.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="act_intent_confidence_floor",
        type=SettingType.FLOAT,
        default="0.85",
        description=(
            "Minimum classifier confidence before a turn may resolve to ACT;"
            " below it the turn degrades to a plain answer. Read live per turn,"
            " so raising this safety floor takes effect without a restart."
        ),
        group="Chat",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
        max_value=1.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="charter_intent_confidence_floor",
        type=SettingType.FLOAT,
        default="0.8",
        description=(
            "Minimum classifier confidence before a turn may resolve to CHARTER;"
            " below it the turn degrades to a plain answer. Read live per turn,"
            " so a change takes effect without a restart."
        ),
        group="Chat",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
        max_value=1.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="turn_intent_temperature",
        type=SettingType.FLOAT,
        default="0.0",
        description=(
            "Sampling temperature for the turn-intent classifier. Read live per"
            " turn, so a change takes effect without a restart."
        ),
        group="Chat",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
        max_value=2.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="turn_intent_max_tokens",
        type=SettingType.INTEGER,
        default="200",
        description=(
            "Token budget for one turn-intent classification reply. Read live"
            " per turn, so a change takes effect without a restart."
        ),
        group="Chat",
        level=SettingLevel.ADVANCED,
        min_value=50,
        max_value=4096,
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="turn_intent_timeout_seconds",
        type=SettingType.FLOAT,
        default="120.0",
        description=(
            "Wall-clock cap for one turn-intent classification call. Read live"
            " per turn, so a change takes effect without a restart."
        ),
        group="Chat",
        level=SettingLevel.ADVANCED,
        min_value=5.0,
        max_value=600.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="narrative_model",
        type=SettingType.MODEL_REF,
        default="",
        description=(
            "Provider + model for the run-narrative prose, selected through"
            " the model picker (a `{provider, model_id}` reference). Empty"
            " keeps the built-in default. Read live per call."
        ),
        group="Models",
        level=SettingLevel.ADVANCED,
    )
)
