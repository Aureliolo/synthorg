"""A spend total says whether it could measure what was spent.

A flat-rate provider records ``cost=0.0`` on every call, which is the correct
number: there is no per-1k price to attribute. The defect is a budget surface
that reads a permanent zero as permanent headroom, so an operator cannot tell
"we have spent nothing" from "this ceiling cannot measure what we are
spending", every deliverable receipt reports zero, and hiring reads an
unmeasurable window as safe.
"""

from datetime import UTC, datetime

import pytest

from synthorg.budget.config import BudgetConfig
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.budget.enums import BudgetAlertLevel
from synthorg.budget.spending_summary import SpendMeasurability
from synthorg.budget.tracker import CostTracker
from synthorg.core.billing_enums import BillingModel
from tests._shared import FakeClock
from tests.unit.budget.conftest import make_cost_record

pytestmark = pytest.mark.unit

_START = datetime(2026, 8, 1, tzinfo=UTC)
_END = datetime(2026, 8, 31, tzinfo=UTC)


class _Resolver:
    """A billing-model resolver over a fixed provider map."""

    def __init__(self, mapping: dict[str, BillingModel]) -> None:
        self._mapping = mapping

    def billing_model_for(self, provider: str) -> BillingModel:
        return self._mapping.get(provider, BillingModel.UNKNOWN)


def _record(
    provider: str,
    *,
    cost: float,
    billing_model: BillingModel = BillingModel.UNKNOWN,
) -> CostRecord:
    return make_cost_record(
        agent_id="agent-1",
        task_id="task-001",
        provider=provider,
        model="example-medium-001",
        input_tokens=1_000,
        output_tokens=500,
        cost=cost,
        currency=DEFAULT_CURRENCY,
        timestamp=datetime(2026, 8, 10, tzinfo=UTC),
        billing_model=billing_model,
    )


def _tracker(resolver: _Resolver | None = None) -> CostTracker:
    tracker = CostTracker(
        budget_config=BudgetConfig(total_monthly=100.0),
        clock=FakeClock(),
    )
    if resolver is not None:
        tracker.bind_billing_model_resolver(resolver)
    return tracker


class TestStamping:
    async def test_the_provider_config_decides_not_the_caller(self) -> None:
        # One owner: the connection's own declaration. A record arriving with
        # a guess is corrected rather than believed, so a caller cannot make
        # spend look measurable by asserting it.
        tracker = _tracker(_Resolver({"flat-gateway": BillingModel.FLAT_RATE}))
        await tracker.record(
            _record("flat-gateway", cost=0.0, billing_model=BillingModel.PER_TOKEN)
        )
        stored = await tracker.get_records()
        assert stored[0].billing_model is BillingModel.FLAT_RATE

    async def test_an_unresolvable_provider_is_unknown_not_per_token(self) -> None:
        # Assuming a ceiling binds when it may not is the failure being fixed.
        tracker = _tracker(_Resolver({}))
        await tracker.record(_record("who-knows", cost=0.0))
        stored = await tracker.get_records()
        assert stored[0].billing_model is BillingModel.UNKNOWN

    async def test_no_resolver_leaves_the_record_as_constructed(self) -> None:
        tracker = _tracker()
        await tracker.record(
            _record("anything", cost=1.0, billing_model=BillingModel.PER_TOKEN)
        )
        stored = await tracker.get_records()
        assert stored[0].billing_model is BillingModel.PER_TOKEN


class TestMeasurability:
    async def test_an_all_flat_rate_window_is_unmeasurable(self) -> None:
        tracker = _tracker(_Resolver({"flat-gateway": BillingModel.FLAT_RATE}))
        await tracker.record(_record("flat-gateway", cost=0.0))

        summary = await tracker.build_summary(start=_START, end=_END)

        assert summary.measurability is SpendMeasurability.UNMEASURABLE
        # 0.0 was the lie: it reads as "we have spent nothing".
        assert summary.budget_used_percent is None

    async def test_a_per_token_window_is_measured(self) -> None:
        tracker = _tracker(_Resolver({"metered": BillingModel.PER_TOKEN}))
        await tracker.record(_record("metered", cost=25.0))

        summary = await tracker.build_summary(start=_START, end=_END)

        assert summary.measurability is SpendMeasurability.MEASURED
        assert summary.budget_used_percent == pytest.approx(25.0)

    async def test_a_mixed_window_says_the_total_understates(self) -> None:
        tracker = _tracker(
            _Resolver(
                {
                    "metered": BillingModel.PER_TOKEN,
                    "flat-gateway": BillingModel.FLAT_RATE,
                }
            )
        )
        await tracker.record(_record("metered", cost=25.0))
        await tracker.record(_record("flat-gateway", cost=0.0))

        summary = await tracker.build_summary(start=_START, end=_END)

        assert summary.measurability is SpendMeasurability.MIXED
        assert summary.budget_used_percent is None
        # The money total is still correct for what it covers.
        assert summary.period.total_cost == pytest.approx(25.0)

    async def test_an_empty_window_is_measured(self) -> None:
        # Nothing was spent AND nothing was hidden, which is not the same
        # claim as "this ceiling cannot see".
        tracker = _tracker(_Resolver({}))

        summary = await tracker.build_summary(start=_START, end=_END)

        assert summary.measurability is SpendMeasurability.MEASURED
        assert summary.budget_used_percent == pytest.approx(0.0)

    async def test_an_unmeasurable_window_is_not_reported_as_comfortable(self) -> None:
        # The alert level is a verdict on headroom against the configured
        # budget, and there is none to give. NORMAL would be the same lie
        # one field over, and is the opposite of what this very condition
        # already yields for the hiring signal.
        tracker = _tracker(_Resolver({"flat-gateway": BillingModel.FLAT_RATE}))
        await tracker.record(_record("flat-gateway", cost=0.0))

        summary = await tracker.build_summary(start=_START, end=_END)

        assert summary.alert_level is BudgetAlertLevel.HARD_STOP


class TestQualifiedTotal:
    """The total and what it covers, read together."""

    @pytest.mark.parametrize(
        ("mapping", "records", "expected"),
        [
            ({"metered": BillingModel.PER_TOKEN}, (("metered", 25.0),), "measured"),
            (
                {"flat-gateway": BillingModel.FLAT_RATE},
                (("flat-gateway", 0.0),),
                "unmeasurable",
            ),
            (
                {
                    "metered": BillingModel.PER_TOKEN,
                    "flat-gateway": BillingModel.FLAT_RATE,
                },
                (("metered", 25.0), ("flat-gateway", 0.0)),
                "mixed",
            ),
            ({}, (), "measured"),
        ],
        ids=["metered", "flat-rate", "mixed", "empty"],
    )
    async def test_the_verdict_matches_the_window(
        self,
        mapping: dict[str, BillingModel],
        records: tuple[tuple[str, float], ...],
        expected: str,
    ) -> None:
        tracker = _tracker(_Resolver(mapping))
        for provider, cost in records:
            await tracker.record(_record(provider, cost=cost))

        qualified = await tracker.get_qualified_total(start=_START, end=_END)

        assert qualified.measurability.value == expected

    async def test_both_halves_come_from_one_window(self) -> None:
        """The number and its verdict are computed from the same rows.

        Read as two queries they take two snapshots, and a record landing
        between them puts one window's verdict on another window's number.
        """
        tracker = _tracker(
            _Resolver(
                {
                    "metered": BillingModel.PER_TOKEN,
                    "flat-gateway": BillingModel.FLAT_RATE,
                }
            )
        )
        await tracker.record(_record("metered", cost=25.0))
        await tracker.record(_record("flat-gateway", cost=0.0))

        qualified = await tracker.get_qualified_total(start=_START, end=_END)

        # The flat-rate row contributes nothing to the total and everything
        # to the verdict, so a total that ignored it would still be 25.0
        # while its verdict said every row was metered.
        assert qualified.cost == pytest.approx(25.0)
        assert qualified.measurability is SpendMeasurability.MIXED
