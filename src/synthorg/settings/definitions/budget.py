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
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="coordination_metrics_max_entries",
        type=SettingType.INTEGER,
        default="10000",
        description=(
            "Maximum coordination-metrics records retained in the"
            " in-memory ring buffer (oldest evicted first). Sizes a"
            " fixed-length deque at store construction; sourced from the"
            " SYNTHORG_BUDGET_COORDINATION_METRICS_MAX_ENTRIES env var >"
            " default at API-process start. Read-only post-init:"
            " resizing the buffer at runtime would discard retained"
            " history, so a change requires a restart."
        ),
        group="Coordination",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        read_only_post_init=True,
        env_var_override="SYNTHORG_BUDGET_COORDINATION_METRICS_MAX_ENTRIES",
        min_value=1,
        max_value=1_000_000,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="benchmark_provider",
        type=SettingType.STRING,
        default="stub",
        description=(
            "Source of per-model benchmark scores for the cost/quality"
            " Pareto frontier and stakes-routing quality floors."
            " ``stub`` uses calibrated per-tier constants (the safe"
            " default; honestly badged as illustrative)."
            " ``measured`` reads measured per-model scores from the"
            " benchmark-score repository, seeded at boot from the"
            " committed recording artifact, falling back to the stub for"
            " any unmeasured model. Sourced from the"
            " SYNTHORG_BUDGET_BENCHMARK_PROVIDER env var > default at"
            " API-process start; the provider is wired once at startup,"
            " so a change requires a restart."
        ),
        group="Cost Dial",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        read_only_post_init=True,
        env_var_override="SYNTHORG_BUDGET_BENCHMARK_PROVIDER",
        validator_pattern=r"^(stub|measured)$",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="model_tier_overrides",
        type=SettingType.JSON,
        default="{}",
        description=(
            "Operator-configured JSON map of model id to quality tier"
            " (``large`` / ``medium`` / ``small`` / ``local-small``),"
            " consulted by the cost/quality Pareto downgrade traversal"
            " before the built-in ``example-<tier>-<rev>`` heuristic."
            " Empty (the default) leaves resolution entirely to the"
            " heuristic, so a normal boot is unchanged. Lets an operator"
            " running arbitrary model ids map them onto a tier so their"
            " measured scores are queried. Sourced from the"
            " SYNTHORG_BUDGET_MODEL_TIER_OVERRIDES env var > default at"
            " API-process start; wired once, so a change needs a restart."
        ),
        group="Cost Dial",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        read_only_post_init=True,
        env_var_override="SYNTHORG_BUDGET_MODEL_TIER_OVERRIDES",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="forecast_required",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Require operator approval of a pre-flight cost forecast before"
            " the work pipeline dispatches a brief. When true, work-entry"
            " adapters create a Forecast row and raise"
            " CostForecastApprovalRequiredError (HTTP 402) until the"
            " operator approves via the queue UI or the inline modal."
            " Disable only for non-interactive intake where the operator"
            " has already approved budget at a higher level."
        ),
        group="Forecast",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="forecast_default_ceiling_multiplier",
        type=SettingType.FLOAT,
        default="1.5",
        description=(
            "Multiplier applied to the forecast upper bound when"
            " suggesting a per-run hard ceiling at approval time."
            " Effective ceiling = forecast.upper_bound *"
            " forecast_default_ceiling_multiplier. Operators may override"
            " the suggestion per brief in the approval UI."
        ),
        group="Forecast",
        level=SettingLevel.ADVANCED,
        min_value=1.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="run_hard_ceiling",
        type=SettingType.FLOAT,
        default="0.0",
        description=(
            "Absolute hard real-money ceiling (in budget.currency) applied"
            " to any run whose Task.hard_ceiling is unset. The in-loop"
            " BudgetChecker raises RunHardCeilingExceededError when the"
            " accumulated cost meets or exceeds this value; the engine"
            " parks the context so the operator can raise the ceiling and"
            " resume. Zero disables the global fallback (per-task ceilings"
            " still apply)."
        ),
        group="Forecast",
        min_value=0.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="forecast_static_prior_per_turn_large",
        type=SettingType.FLOAT,
        default="0.10",
        description=(
            "Static prior cost per turn (in budget.currency) for an agent"
            " on the `large` model tier. Used as the cold-start estimate"
            " when no per-role historical baseline is available; blended"
            " with BaselineStore history via the Bayesian shrinkage"
            " specified by forecast_shrinkage_prior_weight."
        ),
        group="Forecast",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="forecast_static_prior_per_turn_medium",
        type=SettingType.FLOAT,
        default="0.03",
        description=(
            "Static prior cost per turn (in budget.currency) for an agent"
            " on the `medium` model tier. See"
            " forecast_static_prior_per_turn_large for the blend rule."
        ),
        group="Forecast",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="forecast_static_prior_per_turn_small",
        type=SettingType.FLOAT,
        default="0.005",
        description=(
            "Static prior cost per turn (in budget.currency) for an agent"
            " on the `small` model tier. See"
            " forecast_static_prior_per_turn_large for the blend rule."
        ),
        group="Forecast",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="forecast_static_prior_per_turn_local_small",
        type=SettingType.FLOAT,
        default="0.0",
        description=(
            "Static prior cost per turn (in budget.currency) for an agent"
            " on the `local-small` model tier. Defaults to zero because"
            " self-hosted local models incur no provider spend; override"
            " only if the operator's deployment carries an internal"
            " chargeback rate."
        ),
        group="Forecast",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="forecast_shrinkage_prior_weight",
        type=SettingType.FLOAT,
        default="5.0",
        description=(
            "Prior pseudo-count for the Bayesian shrinkage blend in"
            " CostForecaster. Blend formula:"
            " (prior_weight * static_prior + n * historical_mean) /"
            " (prior_weight + n) where n is the number of historical"
            " observations for the role. Larger values pull the estimate"
            " toward the static prior; smaller values trust history more"
            " aggressively."
        ),
        group="Forecast",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="baseline_window_size",
        type=SettingType.INTEGER,
        default="50",
        description=(
            "Sliding-window size for single-agent baseline records used"
            " to derive the multi-agent coordination baselines (Ec, O%,"
            " Ae). Sizes a fixed-length deque at BaselineStore"
            " construction; sourced from the"
            " SYNTHORG_BUDGET_BASELINE_WINDOW_SIZE env var > default at"
            " API-process start. Read-only post-init: resizing the"
            " window at runtime would discard accumulated baselines, so"
            " a change requires a restart."
        ),
        group="Coordination",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        read_only_post_init=True,
        env_var_override="SYNTHORG_BUDGET_BASELINE_WINDOW_SIZE",
        min_value=1,
        max_value=1_000_000,
    )
)
