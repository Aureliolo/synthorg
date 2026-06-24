"""Appearance namespace setting definitions.

The web dashboard is a pure API consumer: it persists no theme/appearance
choice client-side. These settings are the backend source of truth for the
operator's dashboard appearance, hydrated on load and written through the
settings API by every client. Keys mirror the dashboard's theme axes; values
are the same closed sets the frontend renders.
"""

from synthorg.settings.enums import SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_GROUP = "Appearance"

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.APPEARANCE,
        key="color_palette",
        type=SettingType.ENUM,
        default="warm-ops",
        description="Dashboard colour palette.",
        group=_GROUP,
        enum_values=("warm-ops", "ice-station", "stealth", "signal", "neon"),
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.APPEARANCE,
        key="density",
        type=SettingType.ENUM,
        default="balanced",
        description="Layout density.",
        group=_GROUP,
        enum_values=("dense", "balanced", "medium", "sparse"),
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.APPEARANCE,
        key="typography",
        type=SettingType.ENUM,
        default="geist",
        description="Dashboard typeface.",
        group=_GROUP,
        enum_values=("geist", "jetbrains", "ibm-plex"),
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.APPEARANCE,
        key="animation",
        type=SettingType.ENUM,
        default="status-driven",
        description=(
            "Motion preset. The dashboard still honours the operating "
            "system's reduced-motion preference at runtime."
        ),
        group=_GROUP,
        enum_values=("minimal", "spring", "instant", "status-driven", "aggressive"),
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.APPEARANCE,
        key="sidebar_mode",
        type=SettingType.ENUM,
        default="collapsible",
        description="Sidebar display mode.",
        group=_GROUP,
        enum_values=("rail", "collapsible", "hidden", "persistent", "compact"),
    )
)
