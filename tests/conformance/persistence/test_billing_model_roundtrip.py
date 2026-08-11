"""Both-backend round-trip: a row remembers how its provider charged.

``billing_model`` is carried on the row rather than resolved at read time,
for the same reason ``currency`` is: a connection that later changes contract
must not rewrite the history of what was measurable, and one since deleted
must still be answerable. That only holds if both backends actually store and
return it, which is what this pins.
"""

from datetime import UTC, datetime

import pytest

from synthorg.budget.cost_record import CostRecord
from synthorg.budget.spending_summary import SpendMeasurability, measurability_of
from synthorg.core.billing_enums import BillingModel
from synthorg.core.types import NotBlankStr
from synthorg.persistence.cost_record_protocol import CostRecordFilterSpec
from synthorg.persistence.protocol import PersistenceBackend

_AT = datetime(2026, 8, 10, 12, tzinfo=UTC)


def _record(provider: str, billing_model: BillingModel, cost: float) -> CostRecord:
    return CostRecord(
        agent_id=NotBlankStr("agent-billing"),
        provider=NotBlankStr(provider),
        model=NotBlankStr("example-small-001"),
        input_tokens=100,
        output_tokens=50,
        cost=cost,
        currency="EUR",
        timestamp=_AT,
        billing_model=billing_model,
    )


@pytest.mark.integration
class TestBillingModelRoundTrip:
    async def test_each_billing_model_survives_the_round_trip(
        self, backend: PersistenceBackend
    ) -> None:
        for index, billing_model in enumerate(BillingModel):
            await backend.cost_records.append(
                _record(f"provider-{billing_model.value}", billing_model, float(index))
            )

        persisted = await backend.cost_records.query(
            CostRecordFilterSpec(agent_id="agent-billing")
        )

        by_provider = {r.provider: r.billing_model for r in persisted}
        assert by_provider == {f"provider-{m.value}": m for m in BillingModel}

    async def test_a_persisted_window_classifies_as_mixed(
        self, backend: PersistenceBackend
    ) -> None:
        # The read path derives measurability from the rows themselves, so a
        # backend that dropped the column would silently report a flat-rate
        # window as fully measured, which is the defect one layer down.
        await backend.cost_records.append(
            _record("metered", BillingModel.PER_TOKEN, 1.0)
        )
        await backend.cost_records.append(
            _record("flat-gateway", BillingModel.FLAT_RATE, 0.0)
        )

        persisted = await backend.cost_records.query(
            CostRecordFilterSpec(agent_id="agent-billing")
        )

        assert (
            measurability_of(tuple(r.billing_model for r in persisted))
            is SpendMeasurability.MIXED
        )
