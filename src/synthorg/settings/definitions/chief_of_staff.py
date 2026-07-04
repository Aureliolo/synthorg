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
live per LLM call. The one exception is ``direct_mcp_enabled``: letting a
chat instruction drive a real MCP action is fail-closed (it needs security
governance wired at startup) and so stays ``restart_required``.

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
        key="propose_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Let the Chief of Staff clarify a request and park proposed work"
            " items for your approval (the /meta/chat/propose path)."
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
            " in one room (the /meta/chat/group path)."
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
            " agent's trust level (the /meta/chat/act path). Off by default:"
            " the Chief of Staff acts for you only when you opt in."
            " Fail-closed (needs security governance) and restart-required to"
            " enable (the actor is built at startup)."
        ),
        group="Acts on your behalf",
        level=SettingLevel.ADVANCED,
        # lint-allow: restart-required -- security invariant: the acting actor
        # is built fail-closed at startup (needs engine.has_security_governance)
        # with no per-request governance re-check, so a live write must not
        # enable autonomous MCP acting.
        restart_required=True,
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="chat_model",
        type=SettingType.STRING,
        default="",
        description=(
            "Model identifier for Chief-of-Staff chat responses. Empty keeps"
            " the built-in default until setup auto-selects one from your"
            " provider catalogue; set it to override. Read live per call."
        ),
        group="Models",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="propose_model",
        type=SettingType.STRING,
        default="",
        description=(
            "Model identifier for the clarify-and-propose turns. Empty keeps"
            " the built-in default; set it to override. Read live per call."
        ),
        group="Models",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="routing_model",
        type=SettingType.STRING,
        default="",
        description=(
            "Model identifier for the concern-routing classifier. Empty keeps"
            " the built-in default; set it to override. Read live per call."
        ),
        group="Models",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="narrative_model",
        type=SettingType.STRING,
        default="",
        description=(
            "Model identifier for the run-narrative prose. Empty keeps the"
            " built-in default; set it to override. Read live per call."
        ),
        group="Models",
        level=SettingLevel.ADVANCED,
    )
)
