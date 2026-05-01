"""Budget namespace setting definitions."""

from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="total_monthly",
        type=SettingType.FLOAT,
        default="100.0",
        description="Monthly budget limit",
        group="Limits",
        min_value=0.0,
        yaml_path="budget.total_monthly",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="per_task_limit",
        type=SettingType.FLOAT,
        default="5.0",
        description="Maximum cost per task",
        group="Limits",
        min_value=0.0,
        yaml_path="budget.per_task_limit",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="per_agent_daily_limit",
        type=SettingType.FLOAT,
        default="10.0",
        description="Maximum cost per agent per day",
        group="Limits",
        min_value=0.0,
        yaml_path="budget.per_agent_daily_limit",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="auto_downgrade_enabled",
        type=SettingType.BOOLEAN,
        default="false",
        description="Enable automatic model downgrade when budget is low",
        group="Auto-Downgrade",
        level=SettingLevel.ADVANCED,
        yaml_path="budget.auto_downgrade.enabled",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="auto_downgrade_threshold",
        type=SettingType.INTEGER,
        default="85",
        description="Budget usage percent that triggers model downgrade",
        group="Auto-Downgrade",
        level=SettingLevel.ADVANCED,
        min_value=0,
        max_value=100,
        yaml_path="budget.auto_downgrade.threshold",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="reset_day",
        type=SettingType.INTEGER,
        default="1",
        description="Day of month when budget resets (1-28)",
        group="Limits",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=28,
        yaml_path="budget.reset_day",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="alert_warn_at",
        type=SettingType.INTEGER,
        default="75",
        description="Budget usage percent that triggers a warning alert",
        group="Alerts",
        level=SettingLevel.ADVANCED,
        min_value=0,
        max_value=100,
        yaml_path="budget.alerts.warn_at",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="alert_critical_at",
        type=SettingType.INTEGER,
        default="90",
        description="Budget usage percent that triggers a critical alert",
        group="Alerts",
        level=SettingLevel.ADVANCED,
        min_value=0,
        max_value=100,
        yaml_path="budget.alerts.critical_at",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="alert_hard_stop_at",
        type=SettingType.INTEGER,
        default="100",
        description="Budget usage percent that triggers a hard stop",
        group="Alerts",
        level=SettingLevel.ADVANCED,
        min_value=0,
        max_value=100,
        yaml_path="budget.alerts.hard_stop_at",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="currency",
        type=SettingType.STRING,
        default=DEFAULT_CURRENCY,
        description=(
            "ISO 4217 currency code stamped onto every new cost record "
            "and used for display formatting. SynthOrg does not convert "
            "LLM provider costs, so changing this value after data has "
            "accumulated produces mixed-currency history: existing rows "
            "retain their original stamp while subsequent rows carry the "
            "new code. Aggregators across the change window raise "
            "``MixedCurrencyAggregationError`` rather than silently "
            "combining them."
        ),
        group="Display",
        validator_pattern=r"^[A-Z]{3}$",
        yaml_path="budget.currency",
    )
)
