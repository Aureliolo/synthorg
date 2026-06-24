"""Dashboard UI-preference setting definitions.

Miscellaneous dashboard UI preferences that the web client used to keep in
``localStorage``. The dashboard is a pure API consumer: these are the backend
source of truth, hydrated on load and written through the settings API. Only
genuinely per-device ephemeral state (canvas pan/zoom viewport, in-progress
form drafts) stays client-side; everything an operator would expect to follow
their account lives here.
"""

from synthorg.settings.enums import SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_GROUP = "Dashboard"

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.DASHBOARD,
        key="sidebar_collapsed",
        type=SettingType.BOOLEAN,
        default="false",
        description="Whether the dashboard sidebar is collapsed.",
        group=_GROUP,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.DASHBOARD,
        key="command_recents",
        type=SettingType.JSON,
        default="[]",
        description="Recently-used command-palette command ids (most-recent first).",
        group=_GROUP,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.DASHBOARD,
        key="settings_advanced_mode",
        type=SettingType.BOOLEAN,
        default="false",
        description="Whether the Settings page shows advanced settings.",
        group=_GROUP,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.DASHBOARD,
        key="settings_advanced_warned",
        type=SettingType.BOOLEAN,
        default="false",
        description=("Whether the one-time advanced-settings warning has been shown."),
        group=_GROUP,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.DASHBOARD,
        key="tunnel_intro_acknowledged",
        type=SettingType.BOOLEAN,
        default="false",
        description="Whether the operator dismissed the tunnel introduction card.",
        group=_GROUP,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.DASHBOARD,
        key="post_setup_guidance_dismissed",
        type=SettingType.BOOLEAN,
        default="false",
        description="Whether the post-setup guidance card has been dismissed.",
        group=_GROUP,
    )
)
