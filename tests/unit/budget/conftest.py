"""Unit test configuration and fixtures for budget models."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar

import pytest
from polyfactory.factories.pydantic_factory import ModelFactory

from synthorg.budget.call_analytics_config import CallAnalyticsConfig
from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.config import (
    AutoDowngradeConfig,
    BudgetAlertConfig,
    BudgetConfig,
)
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.cost_tiers import CostTierDefinition, CostTiersConfig
from synthorg.budget.currency import DEFAULT_CURRENCY, CurrencyCode
from synthorg.budget.enums import BudgetAlertLevel
from synthorg.budget.hierarchy import (
    BudgetHierarchy,
    DepartmentBudget,
    TeamBudget,
)
from synthorg.budget.optimizer import CostOptimizer
from synthorg.budget.optimizer_models import CostOptimizerConfig
from synthorg.budget.quota import (
    QuotaLimit,
    QuotaWindow,
    SubscriptionConfig,
)
from synthorg.budget.quota_tracker import QuotaTracker
from synthorg.budget.risk_config import RiskBudgetAlertConfig, RiskBudgetConfig
from synthorg.budget.risk_record import RiskRecord
from synthorg.budget.spending_summary import (
    AgentSpending,
    DepartmentSpending,
    PeriodSpending,
    SpendingSummary,
)
from synthorg.budget.tracker import CostTracker
from synthorg.providers.routing.models import ResolvedModel
from synthorg.providers.routing.resolver import ModelResolver
from synthorg.security.risk_scorer import RiskScore

if TYPE_CHECKING:
    from collections.abc import Callable

# ── Factories ──────────────────────────────────────────────────────


class BudgetAlertConfigFactory(ModelFactory[BudgetAlertConfig]):
    __model__ = BudgetAlertConfig
    warn_at = 75
    critical_at = 90
    hard_stop_at = 100


class AutoDowngradeConfigFactory(ModelFactory[AutoDowngradeConfig]):
    __model__ = AutoDowngradeConfig
    enabled = False
    downgrade_map = ()


class RiskBudgetAlertConfigFactory(ModelFactory[RiskBudgetAlertConfig]):
    __model__ = RiskBudgetAlertConfig
    warn_at = 75
    critical_at = 90


class RiskBudgetConfigFactory(ModelFactory[RiskBudgetConfig]):
    __model__ = RiskBudgetConfig
    enabled = False
    per_task_risk_limit = 5.0
    per_agent_daily_risk_limit = 20.0
    total_daily_risk_limit = 100.0
    alerts = RiskBudgetAlertConfigFactory


class BudgetConfigFactory(ModelFactory[BudgetConfig]):
    __model__ = BudgetConfig
    total_monthly = 100.0
    per_task_limit = 5.0
    per_agent_daily_limit = 10.0
    # Pin a known-valid ISO 4217 code; polyfactory's random-string
    # generator would otherwise emit codes outside the formatter
    # allowlist (e.g. "JLF") and fail ``CurrencyCode`` validation.
    currency = DEFAULT_CURRENCY
    alerts = BudgetAlertConfigFactory
    auto_downgrade = AutoDowngradeConfigFactory
    risk_budget = RiskBudgetConfigFactory
    # Pin the quota / call-analytics composites: polyfactory would otherwise
    # synthesise a random ``SubscriptionConfig`` whose cost_model violates the
    # LOCAL monthly_cost invariant.
    subscriptions: ClassVar[dict[str, SubscriptionConfig]] = {}
    call_analytics = CallAnalyticsConfig()


class TeamBudgetFactory(ModelFactory[TeamBudget]):
    __model__ = TeamBudget
    budget_percent = 10.0


class DepartmentBudgetFactory(ModelFactory[DepartmentBudget]):
    __model__ = DepartmentBudget
    budget_percent = 25.0
    teams = ()


class BudgetHierarchyFactory(ModelFactory[BudgetHierarchy]):
    __model__ = BudgetHierarchy
    departments = ()


class CostRecordFactory(ModelFactory[CostRecord]):
    __model__ = CostRecord
    input_tokens = 1000
    output_tokens = 500
    cost = 0.05
    currency = DEFAULT_CURRENCY
    # Ensure retry fields are consistent: no reason without a retry count.
    retry_reason = None


class PeriodSpendingFactory(ModelFactory[PeriodSpending]):
    __model__ = PeriodSpending
    start = datetime(2026, 2, 1, tzinfo=UTC)
    end = datetime(2026, 3, 1, tzinfo=UTC)
    currency = DEFAULT_CURRENCY


class AgentSpendingFactory(ModelFactory[AgentSpending]):
    __model__ = AgentSpending
    currency = DEFAULT_CURRENCY


class DepartmentSpendingFactory(ModelFactory[DepartmentSpending]):
    __model__ = DepartmentSpending
    currency = DEFAULT_CURRENCY


class SpendingSummaryFactory(ModelFactory[SpendingSummary]):
    __model__ = SpendingSummary
    period = PeriodSpendingFactory
    by_agent = ()
    by_department = ()


class CostOptimizerConfigFactory(ModelFactory[CostOptimizerConfig]):
    __model__ = CostOptimizerConfig


class CostTierDefinitionFactory(ModelFactory[CostTierDefinition]):
    __model__ = CostTierDefinition
    sort_order = 0


class CostTiersConfigFactory(ModelFactory[CostTiersConfig]):
    __model__ = CostTiersConfig
    tiers = ()
    include_builtin = True


class QuotaLimitFactory(ModelFactory[QuotaLimit]):
    __model__ = QuotaLimit
    max_requests = 60


class SubscriptionConfigFactory(ModelFactory[SubscriptionConfig]):
    __model__ = SubscriptionConfig
    quotas = ()


# ── Shared risk helpers ────────────────────────────────────────────


def make_risk_score(
    *,
    reversibility: float = 0.5,
    blast_radius: float = 0.3,
    data_sensitivity: float = 0.2,
    external_visibility: float = 0.1,
) -> RiskScore:
    """Build a RiskScore with sensible defaults."""
    return RiskScore(
        reversibility=reversibility,
        blast_radius=blast_radius,
        data_sensitivity=data_sensitivity,
        external_visibility=external_visibility,
    )


def make_risk_record(
    *,
    agent_id: str = "agent-1",
    task_id: str = "task-1",
    action_type: str = "code:write",
    risk_units: float = 0.3,
    timestamp: datetime | None = None,
) -> RiskRecord:
    """Build a RiskRecord with sensible defaults."""
    score = make_risk_score()
    return RiskRecord(
        agent_id=agent_id,
        task_id=task_id,
        action_type=action_type,
        risk_score=score,
        risk_units=risk_units,
        timestamp=timestamp or datetime.now(UTC),
    )


# ── Sample Fixtures ────────────────────────────────────────────────


@pytest.fixture
def sample_budget_config() -> BudgetConfig:
    return BudgetConfig(
        total_monthly=500.0,
        alerts=BudgetAlertConfig(warn_at=70, critical_at=85, hard_stop_at=95),
        per_task_limit=10.0,
        per_agent_daily_limit=25.0,
        auto_downgrade=AutoDowngradeConfig(
            enabled=True,
            threshold=80,
            downgrade_map=(("large", "medium"), ("medium", "small")),
        ),
    )


@pytest.fixture
def sample_cost_record() -> CostRecord:
    return CostRecord(
        agent_id="sarah_chen",
        task_id="task-123",
        provider="example-provider",
        model="test-model-001",
        input_tokens=4500,
        output_tokens=1200,
        cost=0.0315,
        currency=DEFAULT_CURRENCY,
        timestamp=datetime(2026, 2, 27, 10, 30, 0, tzinfo=UTC),
    )


@pytest.fixture
def sample_budget_hierarchy() -> BudgetHierarchy:
    return BudgetHierarchy(
        total_monthly=100.0,
        departments=(
            DepartmentBudget(
                department_name="Engineering",
                budget_percent=50.0,
                teams=(
                    TeamBudget(team_name="Backend", budget_percent=40.0),
                    TeamBudget(team_name="Frontend", budget_percent=30.0),
                ),
            ),
            DepartmentBudget(
                department_name="Product",
                budget_percent=30.0,
                teams=(
                    TeamBudget(team_name="Design", budget_percent=50.0),
                    TeamBudget(team_name="Research", budget_percent=50.0),
                ),
            ),
        ),
    )


@pytest.fixture
def sample_spending_summary() -> SpendingSummary:
    return SpendingSummary(
        period=PeriodSpending(
            start=datetime(2026, 2, 1, tzinfo=UTC),
            end=datetime(2026, 3, 1, tzinfo=UTC),
            total_cost=75.50,
            currency=DEFAULT_CURRENCY,
            total_input_tokens=500000,
            total_output_tokens=120000,
            record_count=150,
        ),
        by_agent=(
            AgentSpending(
                agent_id="sarah_chen",
                total_cost=40.0,
                currency=DEFAULT_CURRENCY,
                total_input_tokens=300000,
                total_output_tokens=80000,
                record_count=80,
            ),
            AgentSpending(
                agent_id="alex_dev",
                total_cost=35.50,
                currency=DEFAULT_CURRENCY,
                total_input_tokens=200000,
                total_output_tokens=40000,
                record_count=70,
            ),
        ),
        by_department=(
            DepartmentSpending(
                department_name="Engineering",
                total_cost=75.50,
                currency=DEFAULT_CURRENCY,
                total_input_tokens=500000,
                total_output_tokens=120000,
                record_count=150,
            ),
        ),
        budget_total_monthly=100.0,
        budget_used_percent=75.5,
        alert_level=BudgetAlertLevel.WARNING,
    )


# ── CostTracker fixtures ─────────────────────────────────────────

_DEPARTMENT_MAP: dict[str, str] = {
    "alice": "Engineering",
    "bob": "Engineering",
    "carol": "Product",
    "dave": "Operations",
}


@pytest.fixture
def budget_config_for_tracker() -> BudgetConfig:
    """Budget config with known thresholds for tracker tests."""
    return BudgetConfig(
        total_monthly=100.0,
        alerts=BudgetAlertConfig(warn_at=75, critical_at=90, hard_stop_at=100),
    )


@pytest.fixture
def department_resolver() -> Callable[[str], str | None]:
    """Maps agent_id → department via a static lookup."""
    return _DEPARTMENT_MAP.get


@pytest.fixture
def cost_tracker(
    budget_config_for_tracker: BudgetConfig,
    department_resolver: Callable[[str], str | None],
) -> CostTracker:
    """CostTracker wired with budget config and department resolver."""
    return CostTracker(
        budget_config=budget_config_for_tracker,
        department_resolver=department_resolver,
    )


def make_quota_tracker(
    *,
    provider: str = "test-provider",
    max_requests: int = 60,
    window: QuotaWindow = QuotaWindow.PER_MINUTE,
) -> QuotaTracker:
    """Build a QuotaTracker with a single provider and quota."""
    sub = SubscriptionConfig(
        quotas=(QuotaLimit(window=window, max_requests=max_requests),),
    )
    return QuotaTracker(subscriptions={provider: sub})


def make_cost_record(  # noqa: PLR0913
    *,
    agent_id: str = "alice",
    task_id: str = "task-001",
    project_id: str | None = None,
    provider: str = "test-provider",
    model: str = "test-model-001",
    input_tokens: int = 1000,
    output_tokens: int = 500,
    cost: float = 0.05,
    currency: CurrencyCode = DEFAULT_CURRENCY,
    timestamp: datetime | None = None,
    call_category: LLMCallCategory | None = None,
) -> CostRecord:
    """Build a CostRecord with sensible defaults."""
    return CostRecord(
        agent_id=agent_id,
        task_id=task_id,
        project_id=project_id,
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost=cost,
        currency=currency,
        timestamp=timestamp or datetime(2026, 2, 15, 12, 0, 0, tzinfo=UTC),
        call_category=call_category,
    )


# ── CostOptimizer test helpers ───────────────────────────────────

OPT_START = datetime(2026, 2, 1, tzinfo=UTC)
OPT_END = datetime(2026, 3, 1, tzinfo=UTC)


def make_optimizer(
    *,
    budget_config: BudgetConfig | None = None,
    config: CostOptimizerConfig | None = None,
    model_resolver: ModelResolver | None = None,
) -> tuple[CostOptimizer, CostTracker]:
    """Build a CostOptimizer with a fresh CostTracker."""
    bc = budget_config or BudgetConfig(total_monthly=100.0)
    tracker = CostTracker(budget_config=bc)
    optimizer = CostOptimizer(
        cost_tracker=tracker,
        budget_config=bc,
        config=config,
        model_resolver=model_resolver,
    )
    return optimizer, tracker


def make_resolver(
    models: list[ResolvedModel] | None = None,
) -> ModelResolver:
    """Build a ModelResolver from a list of ResolvedModel."""
    if models is None:
        models = [
            ResolvedModel(
                provider_name="test-provider",
                model_id="test-large-001",
                alias="large",
                cost_per_1k_input=0.03,
                cost_per_1k_output=0.06,
            ),
            ResolvedModel(
                provider_name="test-provider",
                model_id="test-medium-001",
                alias="medium",
                cost_per_1k_input=0.01,
                cost_per_1k_output=0.02,
            ),
            ResolvedModel(
                provider_name="test-provider",
                model_id="test-small-001",
                alias="small",
                cost_per_1k_input=0.001,
                cost_per_1k_output=0.002,
            ),
        ]
    index: dict[str, tuple[ResolvedModel, ...]] = {}
    for m in models:
        existing = index.get(m.model_id, ())
        index[m.model_id] = (*existing, m)
        if m.alias is not None:
            existing_alias = index.get(m.alias, ())
            index[m.alias] = (*existing_alias, m)
    return ModelResolver(index)
