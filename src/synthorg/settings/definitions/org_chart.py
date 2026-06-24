"""Org-chart view-preference setting definitions.

The web dashboard persists no view preferences client-side; these toggles for
the Org Chart view are the backend source of truth, hydrated on load and
written through the settings API by every client.
"""

from synthorg.settings.enums import SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_GROUP = "Org Chart"

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ORG_CHART,
        key="particle_flow_mode",
        type=SettingType.ENUM,
        default="live",
        description=(
            "Hierarchy-edge particle animation: 'always' animates every edge, "
            "'live' only edges with recent activity, 'off' static lines."
        ),
        group=_GROUP,
        enum_values=("always", "live", "off"),
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ORG_CHART,
        key="show_add_agent_button",
        type=SettingType.BOOLEAN,
        default="true",
        description="Show the inline '+ Add agent' affordance on department cards.",
        group=_GROUP,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ORG_CHART,
        key="show_lead_badge",
        type=SettingType.BOOLEAN,
        default="true",
        description="Show the 'LEAD' badge on the department-head agent.",
        group=_GROUP,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ORG_CHART,
        key="show_budget_bar",
        type=SettingType.BOOLEAN,
        default="true",
        description="Show the budget percent and utilisation bar on department cards.",
        group=_GROUP,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ORG_CHART,
        key="show_status_dots",
        type=SettingType.BOOLEAN,
        default="false",
        description="Show the per-agent status dots row on department cards.",
        group=_GROUP,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ORG_CHART,
        key="show_minimap",
        type=SettingType.BOOLEAN,
        default="false",
        description="Show the minimap in the bottom-right corner of the org chart.",
        group=_GROUP,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ORG_CHART,
        key="collapsed_departments",
        type=SettingType.JSON,
        default="[]",
        description="Collapsed department ids in the org chart, as a JSON array.",
        group=_GROUP,
    )
)
