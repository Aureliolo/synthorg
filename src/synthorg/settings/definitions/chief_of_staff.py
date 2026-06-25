"""Chief-of-Staff namespace setting definitions.

Each Chief-of-Staff capability flag and per-feature model is an
individual runtime setting so the wizard and dashboard Settings can
toggle it over the standard ``/settings`` API. The conversational
capabilities (explain-chat, propose, concern-routing, group-chat)
default on and are live-gated at the controller, so toggling them takes
effect with no restart. The off-by-default capabilities that wire a
boot-time loop or observer (learning, alerts, narrative) are
``restart_required``; the acts-on-your-behalf capabilities (agent invite,
direct MCP acting) default off for security.

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
            " agent instead of always answering as the generic persona. Baked"
            " into the proposer at startup, so a change is restart-required."
        ),
        group="Conversational",
        restart_required=True,
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
            " the background; off by default."
        ),
        group="Automation",
        level=SettingLevel.ADVANCED,
        restart_required=True,
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
            " default."
        ),
        group="Automation",
        level=SettingLevel.ADVANCED,
        restart_required=True,
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
            " default."
        ),
        group="Automation",
        level=SettingLevel.ADVANCED,
        restart_required=True,
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
            " on your behalf only when you opt in. Restart-required to enable"
            " (the coordinator is built at startup)."
        ),
        group="Acts on your behalf",
        level=SettingLevel.ADVANCED,
        restart_required=True,
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
            " provider catalogue; set it to override."
        ),
        group="Models",
        level=SettingLevel.ADVANCED,
        restart_required=True,
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
            " the built-in default; set it to override."
        ),
        group="Models",
        level=SettingLevel.ADVANCED,
        restart_required=True,
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
            " the built-in default; set it to override."
        ),
        group="Models",
        level=SettingLevel.ADVANCED,
        restart_required=True,
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
            " built-in default; set it to override."
        ),
        group="Models",
        level=SettingLevel.ADVANCED,
        restart_required=True,
    )
)
