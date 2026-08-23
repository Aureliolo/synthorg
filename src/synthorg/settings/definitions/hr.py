# module-kind: declarative
"""HR namespace setting definitions.

Covers the tuning knobs for the HR subsystems that derive a department's
dashboard health from real task outcomes.
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

# ── Department health derivation ─────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="department_health_window_days",
        type=SettingType.INTEGER,
        default="7",
        description=(
            "Rolling window (days) of terminal task runs used to derive a"
            " department's dashboard health from real outcomes. Re-read per"
            " health request, so a change applies with no restart."
        ),
        group="Health",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=365,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="department_health_min_runs",
        type=SettingType.INTEGER,
        default="3",
        description=(
            "Minimum terminal task runs in the window before a department"
            " health score is shown. Below this the dashboard shows an"
            " explicit no-data state instead of a misleading number, so"
            " zero-activity departments never read as fully healthy."
        ),
        group="Health",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=1000,
    )
)
