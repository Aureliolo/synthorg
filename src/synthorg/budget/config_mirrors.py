# module-kind: declarative
"""Settings keys each budget config field mirrors.

One row per live knob: which ``budget.*`` setting feeds which field, and
how its stored string becomes a value. Kept beside the models rather than
inside them so the table reads as the flat list it is.
"""

from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import (
    MirrorField,
    parse_bool,
    parse_float,
    parse_int,
    parse_json_str_dict,
)

BUDGET_MIRROR_FIELDS: tuple[MirrorField, ...] = (
    MirrorField(
        field="total_monthly",
        namespace=SettingNamespace.BUDGET,
        key="total_monthly",
        parse=parse_float,
    ),
    MirrorField(
        field="per_task_limit",
        namespace=SettingNamespace.BUDGET,
        key="per_task_limit",
        parse=parse_float,
    ),
    MirrorField(
        field="per_agent_daily_limit",
        namespace=SettingNamespace.BUDGET,
        key="per_agent_daily_limit",
        parse=parse_float,
    ),
    MirrorField(
        field="reset_day",
        namespace=SettingNamespace.BUDGET,
        key="reset_day",
        parse=parse_int,
    ),
    MirrorField(
        field="currency",
        namespace=SettingNamespace.BUDGET,
        key="currency",
    ),
    MirrorField(
        field="forecast_required",
        namespace=SettingNamespace.BUDGET,
        key="forecast_required",
        parse=parse_bool,
    ),
    MirrorField(
        field="forecast_default_ceiling_multiplier",
        namespace=SettingNamespace.BUDGET,
        key="forecast_default_ceiling_multiplier",
        parse=parse_float,
    ),
    MirrorField(
        field="run_hard_ceiling",
        namespace=SettingNamespace.BUDGET,
        key="run_hard_ceiling",
        parse=parse_float,
    ),
    MirrorField(
        field="run_hard_token_ceiling",
        namespace=SettingNamespace.BUDGET,
        key="run_hard_token_ceiling",
        parse=parse_int,
    ),
    MirrorField(
        field="session_token_ceiling",
        namespace=SettingNamespace.BUDGET,
        key="session_token_ceiling",
        parse=parse_int,
    ),
    MirrorField(
        field="forecast_static_prior_per_turn_large",
        namespace=SettingNamespace.BUDGET,
        key="forecast_static_prior_per_turn_large",
        parse=parse_float,
    ),
    MirrorField(
        field="forecast_static_prior_per_turn_medium",
        namespace=SettingNamespace.BUDGET,
        key="forecast_static_prior_per_turn_medium",
        parse=parse_float,
    ),
    MirrorField(
        field="forecast_static_prior_per_turn_small",
        namespace=SettingNamespace.BUDGET,
        key="forecast_static_prior_per_turn_small",
        parse=parse_float,
    ),
    MirrorField(
        field="forecast_static_prior_per_turn_local_small",
        namespace=SettingNamespace.BUDGET,
        key="forecast_static_prior_per_turn_local_small",
        parse=parse_float,
    ),
    MirrorField(
        field="forecast_shrinkage_prior_weight",
        namespace=SettingNamespace.BUDGET,
        key="forecast_shrinkage_prior_weight",
        parse=parse_float,
    ),
    MirrorField(
        field="benchmark_provider",
        namespace=SettingNamespace.BUDGET,
        key="benchmark_provider",
    ),
    MirrorField(
        field="model_tier_overrides",
        namespace=SettingNamespace.BUDGET,
        key="model_tier_overrides",
        parse=parse_json_str_dict,
    ),
)
"""Live ``budget.*`` keys mirrored onto
:class:`~synthorg.budget.config.BudgetConfig`."""
