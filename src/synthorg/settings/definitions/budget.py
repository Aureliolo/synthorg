# module-kind: declarative
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
        default="true",
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
            " in-memory ring buffer (oldest evicted first). Applies"
            " immediately: the buffer is rebuilt at the new bound keeping"
            " the newest records, so raising it costs no history and"
            " lowering it drops only what the next writes would have"
            " evicted."
        ),
        group="Coordination",
        level=SettingLevel.ADVANCED,
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
        default="measured",
        description=(
            "Source of per-model benchmark scores for the cost/quality"
            " Pareto frontier and stakes-routing quality floors."
            " ``measured`` reads measured per-model scores from the"
            " benchmark-score repository, seeded at boot from the"
            " committed recording artifact; a model with no measured"
            " score renders as explicitly absent, never faked. Resolved"
            " through the live settings chain (DB > env > default); a"
            " change rebuilds the benchmark provider + Pareto analyser"
            " and reloads runtime services without a restart."
        ),
        group="Cost Dial",
        level=SettingLevel.ADVANCED,
        env_var_override="SYNTHORG_BUDGET_BENCHMARK_PROVIDER",
        validator_pattern=r"^measured$",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="model_capability_overrides",
        type=SettingType.JSON,
        default="{}",
        description=(
            "Operator-configured JSON map of model id to capability rung"
            " (``basic`` / ``capable`` / ``expert``), consulted by the"
            " cost/quality Pareto downgrade traversal before the built-in"
            " ``example-<capability>-<rev>`` heuristic."
            " Empty (the default) leaves resolution entirely to the"
            " heuristic, so a normal boot is unchanged. Lets an operator"
            " running arbitrary model ids map them onto a rung so their"
            " measured scores are queried. Resolved through the live"
            " settings chain (DB > env > default); a change rebuilds the"
            " benchmark provider + Pareto analyser and reloads runtime"
            " services without a restart."
        ),
        group="Cost Dial",
        level=SettingLevel.ADVANCED,
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
        default="25.0",
        description=(
            "Absolute hard real-money ceiling applied to any run whose"
            " Task.hard_ceiling is unset, compared against the unconverted"
            " provider-cost value (budget.currency relabels, never"
            " converts). The in-loop"
            " BudgetChecker raises RunHardCeilingExceededError when the"
            " accumulated cost meets or exceeds this value; the engine"
            " parks the context so the operator can raise the ceiling and"
            " resume. The shipped default 25.0 is a safety net that caps"
            " otherwise-unbounded runs; 0.0 is the explicit opt-out"
            " sentinel that enforces no global ceiling (per-task ceilings"
            " still apply). Lower or raise it to suit the deployment."
        ),
        group="Forecast",
        min_value=0.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="run_hard_token_ceiling",
        type=SettingType.INTEGER,
        default="50000000",
        description=(
            "Absolute hard token ceiling applied to any run whose"
            " Task.hard_token_ceiling is unset. The money ceiling above is"
            " only a bound where the provider bills per token: against a"
            " flat-rate subscription cost never rises, so it can never fire"
            " and the run's only remaining bound is its turn budget. Tokens"
            " are measured on every provider, so this is the same backstop in"
            " the unit that is always available. The in-loop BudgetChecker"
            " raises RunHardTokenCeilingExceededError when accumulated tokens"
            " meet or exceed this value; the engine parks the context so the"
            " operator can raise the ceiling and resume. The shipped default"
            " allows a full-length run (engine.max_turns 300 with 3"
            " extensions is up to 1200 turns, roughly 48M cumulative tokens"
            " at a large context) and stops a genuine runaway; 0 is the"
            " explicit opt-out that enforces no global ceiling."
        ),
        group="Forecast",
        min_value=0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="session_token_ceiling",
        type=SettingType.INTEGER,
        default="2000000",
        description=(
            "Absolute hard token ceiling for the short helper sessions"
            " (decomposition, plan review, evaluation, retrospective, a chat"
            " action, the react loop's own bound). Each carries its own tuned"
            " money ceiling, which measures nothing against a flat-rate"
            " provider; this is the backstop in the unit that is always"
            " available. One number rather than one per session because it is"
            " a runaway backstop and not a tuning dial: each session already"
            " has its own turn cap. 0 is the explicit opt-out."
        ),
        group="Forecast",
        min_value=0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="forecast_static_prior_per_turn_expert",
        type=SettingType.FLOAT,
        default="0.10",
        description=(
            "Static prior cost per turn, in unconverted provider-cost"
            " units, for an agent"
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
        key="forecast_static_prior_per_turn_capable",
        type=SettingType.FLOAT,
        default="0.03",
        description=(
            "Static prior cost per turn, in unconverted provider-cost"
            " units, for an agent"
            " on the `medium` model tier. See"
            " forecast_static_prior_per_turn_expert for the blend rule."
        ),
        group="Forecast",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="forecast_static_prior_per_turn_basic",
        type=SettingType.FLOAT,
        default="0.005",
        description=(
            "Static prior cost per turn, in unconverted provider-cost"
            " units, for an agent"
            " on the `small` model tier. See"
            " forecast_static_prior_per_turn_expert for the blend rule."
        ),
        group="Forecast",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="forecast_static_prior_per_turn_local",
        type=SettingType.FLOAT,
        default="0.0",
        description=(
            "Static prior cost per turn, in unconverted provider-cost"
            " units, for an agent on a locally hosted model (the `local`"
            " cost bucket). Defaults to zero because"
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
            " Ae). Applies immediately: the window is rebuilt at the new"
            " size keeping the newest records, so the baselines stay"
            " computable across the change."
        ),
        group="Coordination",
        level=SettingLevel.ADVANCED,
        env_var_override="SYNTHORG_BUDGET_BASELINE_WINDOW_SIZE",
        min_value=1,
        max_value=1_000_000,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="quota_poller_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Whether the proactive quota poller runs. When active it polls"
            " provider subscription usage on a fixed cadence and dispatches"
            " WARNING/CRITICAL notifications as thresholds are crossed."
        ),
        group="Quota Poller",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="quota_poll_interval_seconds",
        type=SettingType.FLOAT,
        default="60.0",
        description="How often the quota poller samples provider usage.",
        group="Quota Poller",
        level=SettingLevel.ADVANCED,
        min_value=0.1,
        max_value=3600.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="quota_cooldown_seconds",
        type=SettingType.FLOAT,
        default="300.0",
        description=(
            "Silence window after a quota alert fires before the same"
            " provider/window/level tuple may alert again."
        ),
        group="Quota Poller",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="quota_warn_pct",
        type=SettingType.FLOAT,
        default="80.0",
        description="Provider usage percent that raises a WARNING quota alert.",
        group="Quota Poller",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
        max_value=100.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="quota_critical_pct",
        type=SettingType.FLOAT,
        default="95.0",
        description="Provider usage percent that raises a CRITICAL quota alert.",
        group="Quota Poller",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
        max_value=100.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="risk_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description="Whether cumulative risk-unit budget tracking is active.",
        group="Risk Budget",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="risk_per_task_limit",
        type=SettingType.FLOAT,
        default="5.0",
        description="Maximum cumulative risk units a single task may accrue.",
        group="Risk Budget",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="risk_per_agent_daily",
        type=SettingType.FLOAT,
        default="20.0",
        description="Maximum cumulative risk units per agent per day.",
        group="Risk Budget",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="risk_total_daily",
        type=SettingType.FLOAT,
        default="100.0",
        description="Maximum cumulative risk units across the org per day.",
        group="Risk Budget",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="risk_warn_pct",
        type=SettingType.INTEGER,
        default="75",
        description="Risk budget utilisation percent that raises a warning.",
        group="Risk Budget",
        level=SettingLevel.ADVANCED,
        min_value=0,
        max_value=100,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="risk_critical_pct",
        type=SettingType.INTEGER,
        default="90",
        description="Risk budget utilisation percent that raises a critical alert.",
        group="Risk Budget",
        level=SettingLevel.ADVANCED,
        min_value=0,
        max_value=100,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BUDGET,
        key="report_retention_days",
        type=SettingType.INTEGER,
        default="90",
        description="How long automated budget reports are retained.",
        group="Reporting",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=365,
    )
)
