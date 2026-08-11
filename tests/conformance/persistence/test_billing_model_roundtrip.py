"""Both-backend round-trip: a row remembers how its provider charged.

``billing_model`` is carried on the row rather than resolved at read time,
for the same reason ``currency`` is: a connection that later changes contract
must not rewrite the history of what was measurable, and one since deleted
must still be answerable. That only holds if both backends actually store and
return it, which is what this pins.
"""

import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest

from synthorg.budget.cost_record import CostRecord
from synthorg.budget.spending_summary import SpendMeasurability, measurability_of
from synthorg.core.billing_enums import BillingModel
from synthorg.core.types import NotBlankStr
from synthorg.persistence.cost_record_protocol import CostRecordFilterSpec
from synthorg.persistence.protocol import PersistenceBackend

if TYPE_CHECKING:
    import aiosqlite

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


_RAW_INSERT_SQLITE = """
INSERT INTO cost_records (
    agent_id, provider, model, input_tokens, output_tokens, cost,
    currency, timestamp, billing_model
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_RAW_INSERT_POSTGRES = """
INSERT INTO cost_records (
    agent_id, provider, model, input_tokens, output_tokens, cost,
    currency, timestamp, billing_model
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

_RAW_VALUES: tuple[object, ...] = (
    "agent-raw",
    "provider-raw",
    "example-small-001",
    1,
    1,
    0.0,
    "EUR",
    _AT,
)


async def _insert_raw_billing_model(
    backend: PersistenceBackend, billing_model: str
) -> None:
    """Insert a cost row bypassing the typed boundary, expecting a refusal.

    Raises:
        AssertionError: If the database accepts the value.
        ValueError: If the backend is neither of the two supported kinds.
    """
    handle = backend.get_db()
    if backend.backend_name == "sqlite":
        connection = cast("aiosqlite.Connection", handle)
        with pytest.raises(sqlite3.IntegrityError):
            await connection.execute(_RAW_INSERT_SQLITE, (*_RAW_VALUES, billing_model))
        await connection.rollback()
        return
    if backend.backend_name == "postgres":
        from psycopg import errors
        from psycopg_pool import AsyncConnectionPool

        pool = cast("AsyncConnectionPool", handle)
        async with pool.connection() as conn, conn.cursor() as cur:
            with pytest.raises(errors.CheckViolation):
                await cur.execute(_RAW_INSERT_POSTGRES, (*_RAW_VALUES, billing_model))
        return
    msg = f"Unknown backend: {backend.backend_name}"
    raise ValueError(msg)


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

    async def test_the_check_rejects_a_value_outside_the_enum(
        self, backend: PersistenceBackend
    ) -> None:
        """The column's membership CHECK is exercised, not merely declared.

        Nothing in the application can reach it: the field is typed, so an
        invalid value is unconstructible and the repository never sees one.
        That is the right posture and it also means the constraint has no
        coverage from any app-path test, so it is reached here through the
        migrated handle directly. Without this the CHECK could be dropped
        from one backend's schema and every other test would still pass.
        """
        await _insert_raw_billing_model(backend, "not-a-billing-model")
