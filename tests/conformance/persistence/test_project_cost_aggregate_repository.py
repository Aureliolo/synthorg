"""Conformance tests for ``ProjectCostAggregateRepository``.

Covers atomic increment semantics and lookup parity between SQLite and
Postgres. Cost-ceiling enforcement reads aggregates on every budget
check, so both backends are driven behind the shared ``backend`` fixture
to catch backend-specific divergence.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration

_TTL_SECONDS = 3600.0


class TestProjectCostAggregateRepository:
    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        result = await backend.project_cost_aggregates.get(
            NotBlankStr("never-touched"),
        )
        assert result is None

    async def test_increment_creates_row(self, backend: PersistenceBackend) -> None:
        aggregate = await backend.project_cost_aggregates.increment(
            NotBlankStr("proj-001"),
            cost=0.25,
            input_tokens=100,
            output_tokens=50,
            currency="USD",
        )
        assert aggregate.project_id == "proj-001"
        assert aggregate.total_cost == pytest.approx(0.25)
        assert aggregate.currency == "USD"
        assert aggregate.total_input_tokens == 100
        assert aggregate.total_output_tokens == 50
        assert aggregate.record_count == 1

    async def test_increment_accumulates(self, backend: PersistenceBackend) -> None:
        await backend.project_cost_aggregates.increment(
            NotBlankStr("proj-001"),
            cost=0.10,
            input_tokens=50,
            output_tokens=25,
            currency="USD",
        )
        updated = await backend.project_cost_aggregates.increment(
            NotBlankStr("proj-001"),
            cost=0.15,
            input_tokens=30,
            output_tokens=20,
            currency="USD",
        )

        assert updated.total_cost == pytest.approx(0.25)
        assert updated.total_input_tokens == 80
        assert updated.total_output_tokens == 45
        assert updated.record_count == 2

    async def test_get_after_increment(self, backend: PersistenceBackend) -> None:
        await backend.project_cost_aggregates.increment(
            NotBlankStr("proj-002"),
            cost=1.00,
            input_tokens=1000,
            output_tokens=500,
            currency="USD",
        )

        fetched = await backend.project_cost_aggregates.get(NotBlankStr("proj-002"))
        assert fetched is not None
        assert fetched.total_cost == pytest.approx(1.00)

    async def test_increments_are_project_scoped(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.project_cost_aggregates.increment(
            NotBlankStr("alpha"),
            cost=0.50,
            input_tokens=10,
            output_tokens=5,
            currency="USD",
        )
        await backend.project_cost_aggregates.increment(
            NotBlankStr("beta"),
            cost=2.00,
            input_tokens=40,
            output_tokens=20,
            currency="USD",
        )

        alpha = await backend.project_cost_aggregates.get(NotBlankStr("alpha"))
        beta = await backend.project_cost_aggregates.get(NotBlankStr("beta"))
        assert alpha is not None
        assert beta is not None
        assert alpha.total_cost == pytest.approx(0.50)
        assert beta.total_cost == pytest.approx(2.00)

    async def test_increment_rejects_currency_mismatch(
        self, backend: PersistenceBackend
    ) -> None:
        from synthorg.budget.errors import (
            MixedCurrencyAggregationError,
        )

        await backend.project_cost_aggregates.increment(
            NotBlankStr("gamma"),
            cost=1.0,
            input_tokens=10,
            output_tokens=5,
            currency="USD",
        )
        with pytest.raises(MixedCurrencyAggregationError) as exc_info:
            await backend.project_cost_aggregates.increment(
                NotBlankStr("gamma"),
                cost=1.0,
                input_tokens=10,
                output_tokens=5,
                currency="EUR",
            )
        assert exc_info.value.currencies == frozenset({"USD", "EUR"})
        assert exc_info.value.project_id == "gamma"

        # The mismatched-currency rejection must fail-closed: the
        # durable aggregate must remain at the first-write state, with
        # no partial cost / token / count update from the rejected
        # call.  Without this guarantee a retry (or the operator
        # adjusting their currency) would double-count the entry.
        after = await backend.project_cost_aggregates.get(NotBlankStr("gamma"))
        assert after is not None
        assert after.currency == "USD"
        assert after.total_cost == pytest.approx(1.0)
        assert after.total_input_tokens == 10
        assert after.total_output_tokens == 5
        assert after.record_count == 1

    async def test_increment_if_unseen_first_call_applies(
        self, backend: PersistenceBackend
    ) -> None:
        aggregate, was_new = await backend.project_cost_aggregates.increment_if_unseen(
            NotBlankStr("uns-001"),
            cost=0.40,
            input_tokens=20,
            output_tokens=10,
            currency="USD",
            claim_id=NotBlankStr("claim-a"),
            now=datetime.now(UTC),
            ttl_seconds=_TTL_SECONDS,
        )
        assert was_new is True
        assert aggregate is not None
        assert aggregate.total_cost == pytest.approx(0.40)
        assert aggregate.record_count == 1

    async def test_increment_if_unseen_duplicate_skips(
        self, backend: PersistenceBackend
    ) -> None:
        first, first_new = await backend.project_cost_aggregates.increment_if_unseen(
            NotBlankStr("uns-002"),
            cost=0.40,
            input_tokens=20,
            output_tokens=10,
            currency="USD",
            claim_id=NotBlankStr("claim-dup"),
            now=datetime.now(UTC),
            ttl_seconds=_TTL_SECONDS,
        )
        assert first_new is True
        assert first is not None

        # A redelivery of the same claim must not re-bill.
        second, second_new = await backend.project_cost_aggregates.increment_if_unseen(
            NotBlankStr("uns-002"),
            cost=0.40,
            input_tokens=20,
            output_tokens=10,
            currency="USD",
            claim_id=NotBlankStr("claim-dup"),
            now=datetime.now(UTC),
            ttl_seconds=_TTL_SECONDS,
        )
        assert second_new is False
        assert second is None

        fetched = await backend.project_cost_aggregates.get(NotBlankStr("uns-002"))
        assert fetched is not None
        assert fetched.total_cost == pytest.approx(0.40)
        assert fetched.record_count == 1

    async def test_increment_if_unseen_distinct_claims_accumulate(
        self, backend: PersistenceBackend
    ) -> None:
        for claim in ("c1", "c2"):
            _, was_new = await backend.project_cost_aggregates.increment_if_unseen(
                NotBlankStr("uns-003"),
                cost=0.10,
                input_tokens=5,
                output_tokens=5,
                currency="USD",
                claim_id=NotBlankStr(claim),
                now=datetime.now(UTC),
                ttl_seconds=_TTL_SECONDS,
            )
            assert was_new is True

        fetched = await backend.project_cost_aggregates.get(NotBlankStr("uns-003"))
        assert fetched is not None
        assert fetched.total_cost == pytest.approx(0.20)
        assert fetched.record_count == 2
