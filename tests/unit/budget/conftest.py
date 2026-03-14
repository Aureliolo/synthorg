"""Unit test configuration and fixtures for budget models."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from polyfactory.factories.pydantic_factory import ModelFactory

from synthorg.budget.config import (
    AutoDowngradeConfig,
    BudgetAlertConfig,
    BudgetConfig,
)
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.cost_tiers import CostTierDefinition, CostTiersConfig
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
from synthorg.budget.reports import ReportGenerator
from synthorg.budget.spending_summary import (
    AgentSpending,
    DepartmentSpending,
    PeriodSpending,
    SpendingSummary,
)
from synthorg.budget.tracker import CostTracker
from synthorg.providers.routing.models import ResolvedModel
from synthorg.providers.routing.resolver import ModelResolver

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


class BudgetConfigFactory(ModelFactory[BudgetConfig]):
    __model__ = BudgetConfig
    total_monthly = 100.0
    per_task_limit = 5.0
    per_agent_daily_limit = 10.0
    alerts = BudgetAlertConfigFactory
    auto_downgrade = AutoDowngradeConfigFactory


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
    cost_usd = 0.05


class PeriodSpendingFactory(ModelFactory[PeriodSpending]):
    __model__ = PeriodSpending
    start = datetime(2026, 2, 1, tzinfo=UTC)
    end = datetime(2026, 3, 1, tzinfo=UTC)


class AgentSpendingFactory(ModelFactory[AgentSpending]):
    __model__ = AgentSpending


class DepartmentSpendingFactory(ModelFactory[DepartmentSpending]):
    __model__ = DepartmentSpending


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
        cost_usd=0.0315,
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
            total_cost_usd=75.50,
            total_input_tokens=500000,
            total_output_tokens=120000,
            record_count=150,
        ),
        by_agent=(
            AgentSpending(
                agent_id="sarah_chen",
                total_cost_usd=40.0,
                total_input_tokens=300000,
                total_output_tokens=80000,
                record_count=80,
            ),
            AgentSpending(
                agent_id="alex_dev",
                total_cost_usd=35.50,
                total_input_tokens=200000,
                total_output_tokens=40000,
                record_count=70,
            ),
        ),
        by_department=(
            DepartmentSpending(
                department_name="Engineering",
                total_cost_usd=75.50,
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
    provider: str = "test-provider",
    model: str = "test-model-001",
    input_tokens: int = 1000,
    output_tokens: int = 500,
    cost_usd: float = 0.05,
    timestamp: datetime | None = None,
) -> CostRecord:
    """Build a CostRecord with sensible defaults."""
    return CostRecord(
        agent_id=agent_id,
        task_id=task_id,
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        timestamp=timestamp or datetime(2026, 2, 15, 12, 0, 0, tzinfo=UTC),
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
    index: dict[str, ResolvedModel] = {}
    for m in models:
        index[m.model_id] = m
        if m.alias is not None:
            index[m.alias] = m
    return ModelResolver(index)


# ── CFO / CostOptimizer fixtures ─────────────────────────────────


@pytest.fixture
def cost_optimizer_config() -> CostOptimizerConfig:
    """Default CostOptimizerConfig for tests."""
    return CostOptimizerConfig()


@pytest.fixture
def cost_optimizer(
    budget_config_for_tracker: BudgetConfig,
    cost_tracker: CostTracker,
    cost_optimizer_config: CostOptimizerConfig,
) -> CostOptimizer:
    """CostOptimizer wired with tracker and config."""
    return CostOptimizer(
        cost_tracker=cost_tracker,
        budget_config=budget_config_for_tracker,
        config=cost_optimizer_config,
    )


@pytest.fixture
def report_generator(
    budget_config_for_tracker: BudgetConfig,
    cost_tracker: CostTracker,
) -> ReportGenerator:
    """ReportGenerator wired with tracker and config."""
    return ReportGenerator(
        cost_tracker=cost_tracker,
        budget_config=budget_config_for_tracker,
    )
