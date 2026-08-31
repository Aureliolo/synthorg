# module-kind: tests
"""Cost is real or explicitly unavailable, never a silent zero.

Every journalled unit used to record ``cost: 0.0`` whether or not the
connection behind it priced its calls, because subscription auth returns no
per-call price and nothing distinguished "measured and free" from "nothing
priced this call". ``sum_costs`` and ``Provenance.cost_basis`` are the fix: a
single unpriced component poisons an aggregate rather than being silently
folded in as zero, and the whole-sweep claim travels on the artifact rather
than being guessed from a stored total after the fact.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from evals.recursion_depth.emit import (
    REPORT_MARKDOWN_NAME,
    assemble_report,
    derived_caveats,
    write_report,
)
from evals.recursion_depth.manifest import Arm, Independence, ModelPair
from evals.recursion_depth.models import (
    LEAF,
    CellRecord,
    CostBasis,
    Provenance,
    SpendSource,
    UnitRecord,
    sum_costs,
)
from evals.recursion_depth.provenance import provider_is_priced
from evals.recursion_depth.session import session_spend
from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.currency import CurrencyCode
from synthorg.config.provider_schema import ProviderConfig
from synthorg.config.schema import RootConfig
from synthorg.core.billing_enums import BillingModel
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit

_PAIR = ModelPair(
    provider=NotBlankStr("example-provider"),
    model_id=NotBlankStr("example-capable-001"),
    capability="capable",
    family=NotBlankStr("example-family-a"),
)


def _company_config(*, billing_model: BillingModel | None) -> RootConfig:
    """Build a config whose one connection carries *billing_model*, or none.

    Returns:
        The config, with an empty providers block when *billing_model* is
        ``None``, so ``provider_is_priced`` sees an unresolvable connection.
    """
    if billing_model is None:
        return RootConfig(company_name=NotBlankStr("Unresolvable"))
    return RootConfig(
        company_name=NotBlankStr("Priced"),
        providers={
            _PAIR.provider: ProviderConfig(
                connection_name=NotBlankStr(_PAIR.provider),
                billing_model=billing_model,
            )
        },
    )


def _record(*, cost: float, input_tokens: int, output_tokens: int) -> CostRecord:
    """Build one productive call at *cost*.

    Returns:
        The record.
    """
    return CostRecord(
        provider=_PAIR.provider,
        model=_PAIR.model_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost=cost,
        currency=CurrencyCode("USD"),
        timestamp=datetime(2026, 8, 30, tzinfo=UTC),
        call_category=LLMCallCategory.PRODUCTIVE,
    )


class TestSumCosts:
    """One ``None`` poisons the fold; no ``None`` sums like plain addition."""

    def test_no_component_sums_like_ordinary_addition(self) -> None:
        assert sum_costs([1.0, 2.5, 0.5]) == pytest.approx(4.0)

    def test_a_single_none_poisons_the_total(self) -> None:
        """A priced session and an unpriced one have no honest sum."""
        assert sum_costs([1.0, None, 2.0]) is None

    def test_an_empty_iterable_sums_to_zero(self) -> None:
        assert sum_costs([]) == 0.0


class TestProviderIsPriced:
    """The predicate ``session_spend`` and ``Provenance.cost_basis`` share."""

    def test_a_per_token_connection_is_priced(self) -> None:
        config = _company_config(billing_model=BillingModel.PER_TOKEN)

        assert provider_is_priced(_PAIR, company_config=config)

    def test_a_flat_rate_connection_is_not_priced(self) -> None:
        config = _company_config(billing_model=BillingModel.FLAT_RATE)

        assert not provider_is_priced(_PAIR, company_config=config)

    def test_an_unknown_billing_model_is_not_priced(self) -> None:
        """The declared default, and the shape a subscription connection takes."""
        config = _company_config(billing_model=BillingModel.UNKNOWN)

        assert not provider_is_priced(_PAIR, company_config=config)

    def test_an_unresolvable_connection_is_not_priced(self) -> None:
        """Cautious rather than optimistic: an unknown connection is not assumed
        free to measure."""
        config = _company_config(billing_model=None)

        assert not provider_is_priced(_PAIR, company_config=config)


class TestSessionSpendReportsAnHonestCost:
    """The same records, read against a priced and an unpriced connection."""

    def test_an_unpriced_connection_reports_no_cost_but_real_tokens(self) -> None:
        records = (
            _record(cost=0.0, input_tokens=100, output_tokens=200),
            _record(cost=0.0, input_tokens=50, output_tokens=25),
        )

        spent = session_spend(records, gateway_hosted=False, label="cell", priced=False)

        assert spent.cost is None
        assert spent.input_tokens == 150
        assert spent.output_tokens == 225
        assert spent.tokens == 375

    def test_a_priced_connection_reports_the_ledger_sum(self) -> None:
        records = (
            _record(cost=0.4, input_tokens=100, output_tokens=200),
            _record(cost=0.6, input_tokens=50, output_tokens=25),
        )

        spent = session_spend(records, gateway_hosted=False, label="cell", priced=True)

        assert spent.cost == pytest.approx(1.0)
        assert spent.tokens == 375


class TestDerivedCaveats:
    """The report says why every cost figure is absent, in one place."""

    def test_an_unpriced_basis_states_it(self) -> None:
        caveats = derived_caveats(
            (), spend_source=SpendSource.JOURNALLED, cost_basis=CostBasis.UNPRICED
        )

        assert any("does not price" in caveat for caveat in caveats)

    def test_a_priced_basis_states_nothing(self) -> None:
        caveats = derived_caveats(
            (), spend_source=SpendSource.JOURNALLED, cost_basis=CostBasis.PRICED
        )

        assert not any("does not price" in caveat for caveat in caveats)


def _provenance(*, cost_basis: CostBasis) -> Provenance:
    """The sweep-level stamp a report of one basis carries.

    Returns:
        The provenance.
    """
    return Provenance(
        git_commit=NotBlankStr("0" * 40),
        git_dirty=False,
        generated_at=datetime(2026, 8, 30, tzinfo=UTC),
        manifest_sha256=NotBlankStr("sha256:" + "0" * 64),
        spec_id=NotBlankStr("sqlcsv"),
        requirement_count=4,
        executor=_PAIR,
        reviewer=_PAIR,
        independence=Independence.SAME_FAMILY,
        cost_basis=cost_basis,
    )


def _cell(*, cost: float | None) -> CellRecord:
    """One cap-1 run whose single leaf carries *cost*.

    Returns:
        The cell.
    """
    return CellRecord(
        depth_cap=1,
        arm=Arm.GATED,
        repetition=0,
        achieved_depth=1,
        units=(
            UnitRecord(
                unit_id=NotBlankStr("leaf-a"),
                title=NotBlankStr("Build a thing"),
                kind=LEAF,
                depth=0,
                delivered=True,
                attempts=1,
                turns=10,
                cost=cost,
            ),
        ),
        merged_passing=(),
    )


def _markdown(tmp_path: Path, *, cost_basis: CostBasis, cost: float | None) -> str:
    """Render the report for one cell under *cost_basis*.

    Returns:
        The Markdown.
    """
    report = assemble_report(
        provenance=_provenance(cost_basis=cost_basis),
        cells=(_cell(cost=cost),),
        caveats=derived_caveats(
            (), spend_source=SpendSource.JOURNALLED, cost_basis=cost_basis
        ),
        planned_cells=1,
    )
    write_report(report, tmp_path)
    return (tmp_path / REPORT_MARKDOWN_NAME).read_text(encoding="utf-8")


class TestTheReportStatesTheBasis:
    """No row silently reads a cost figure that is not there."""

    def test_an_unpriced_recording_states_it_rather_than_printing_zero(
        self, tmp_path: Path
    ) -> None:
        text = _markdown(tmp_path, cost_basis=CostBasis.UNPRICED, cost=None)
        lines = text.splitlines()

        assert any(
            line.startswith("- Total spend:") and "unpriced" in line for line in lines
        )
        curve_row = next(line for line in lines if line.startswith("| 1 | gated |"))
        assert "unpriced" in curve_row

    def test_a_priced_recording_prints_the_real_figure(self, tmp_path: Path) -> None:
        text = _markdown(tmp_path, cost_basis=CostBasis.PRICED, cost=1.25)

        assert "1.2500" in text
