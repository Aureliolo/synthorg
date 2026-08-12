"""The production answer to "how does this connection charge".

The ledger stamps every row from this snapshot, so its fallback is the one
that decides what an unrecognised provider label is recorded as. Proving the
fallback against a hand-rolled double proves the double.
"""

from datetime import UTC, datetime

import pytest

from synthorg.budget.config import BudgetConfig
from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.budget.tracker import CostTracker
from synthorg.config.provider_schema import ProviderConfig
from synthorg.core.billing_enums import BillingModel
from synthorg.providers.billing_model_snapshot import ProviderBillingModelSnapshot
from tests._shared import FakeClock
from tests.unit.budget.conftest import make_cost_record

pytestmark = pytest.mark.unit


def _config(billing_model: BillingModel) -> ProviderConfig:
    return ProviderConfig(
        driver="scripted",
        connection_name="conn",
        billing_model=billing_model,
    )


class TestSnapshotResolution:
    def test_a_configured_connection_answers_with_its_own_declaration(self) -> None:
        snapshot = ProviderBillingModelSnapshot(
            {
                "flat-gateway": _config(BillingModel.FLAT_RATE),
                "metered": _config(BillingModel.PER_TOKEN),
            }
        )

        assert snapshot.billing_model_for("flat-gateway") is BillingModel.FLAT_RATE
        assert snapshot.billing_model_for("metered") is BillingModel.PER_TOKEN

    def test_a_label_that_is_not_a_connection_is_unknown(self) -> None:
        # A provider deleted since the call, or a label that was never a
        # connection. Assuming per-token here would report an unmeasurable
        # window as measured, which is the failure this field exists for.
        snapshot = ProviderBillingModelSnapshot(
            {"metered": _config(BillingModel.PER_TOKEN)}
        )

        assert snapshot.billing_model_for("who-knows") is BillingModel.UNKNOWN

    def test_an_empty_provider_set_answers_unknown_rather_than_raising(self) -> None:
        assert (
            ProviderBillingModelSnapshot({}).billing_model_for("any")
            is BillingModel.UNKNOWN
        )

    def test_the_snapshot_does_not_follow_the_set_it_was_built_from(self) -> None:
        # It is a snapshot on purpose: the ledger reads it synchronously, and
        # a rebuild is how a provider edit reaches it.
        configs = {"metered": _config(BillingModel.PER_TOKEN)}
        snapshot = ProviderBillingModelSnapshot(configs)
        configs["late-arrival"] = _config(BillingModel.FLAT_RATE)

        assert snapshot.billing_model_for("late-arrival") is BillingModel.UNKNOWN


class TestSnapshotDrivesTheLedger:
    async def test_the_ledger_stamps_from_the_production_snapshot(self) -> None:
        tracker = CostTracker(
            budget_config=BudgetConfig(total_monthly=100.0),
            clock=FakeClock(),
        )
        tracker.bind_billing_model_resolver(
            ProviderBillingModelSnapshot(
                {"flat-gateway": _config(BillingModel.FLAT_RATE)}
            )
        )

        await tracker.record(
            make_cost_record(
                agent_id="agent-1",
                task_id="task-001",
                provider="flat-gateway",
                model="example-medium-001",
                input_tokens=1_000,
                output_tokens=500,
                cost=0.0,
                currency=DEFAULT_CURRENCY,
                timestamp=datetime(2026, 8, 10, tzinfo=UTC),
                billing_model=BillingModel.PER_TOKEN,
            )
        )
        stored = await tracker.get_records()

        assert stored[0].billing_model is BillingModel.FLAT_RATE
