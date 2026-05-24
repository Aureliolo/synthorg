"""Tests for the evolution signal aggregator."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.meta.evolution.outcome_store import (
    InMemoryEvolutionOutcomeStore,
)
from synthorg.meta.signals.evolution import EvolutionSignalAggregator


@pytest.mark.unit
class TestEvolutionSignalAggregator:
    """Aggregator defers to the outcome store's summarize method."""

    async def test_no_store_yields_empty(self) -> None:
        agg = EvolutionSignalAggregator()
        now = datetime.now(UTC)
        summary = await agg.aggregate(
            since=now - timedelta(hours=1),
            until=now,
        )
        assert summary.total_proposals == 0

    async def test_domain_is_evolution(self) -> None:
        agg = EvolutionSignalAggregator()
        assert agg.domain == "evolution"

    async def test_queries_real_store(self) -> None:
        store = InMemoryEvolutionOutcomeStore()
        now = datetime.now(UTC)
        await store.record(
            agent_id=NotBlankStr("a"),
            axis=NotBlankStr("prompt_tuning"),
            applied=True,
            proposed_at=now - timedelta(minutes=30),
        )
        agg = EvolutionSignalAggregator(store)
        summary = await agg.aggregate(
            since=now - timedelta(hours=1),
            until=now + timedelta(minutes=1),
        )
        assert summary.total_proposals == 1
        assert summary.approval_rate == 1.0

    async def test_swallows_store_errors_returns_empty(self) -> None:
        class _ExplodingStore:
            async def record(self, **_kwargs: object) -> None:
                return None

            async def query(self, **_kwargs: object) -> tuple[object, ...]:
                return ()

            async def summarize(self, **_kwargs: object) -> object:
                msg = "boom"
                raise RuntimeError(msg)

            async def count(self) -> int:
                return 0

            async def clear(self) -> None:
                return None

        agg = EvolutionSignalAggregator(_ExplodingStore())  # type: ignore[arg-type]
        now = datetime.now(UTC)
        summary = await agg.aggregate(
            since=now - timedelta(hours=1),
            until=now,
        )
        assert summary.total_proposals == 0

    @pytest.mark.parametrize("exc_cls", [MemoryError, RecursionError])
    async def test_propagates_catastrophic_interpreter_errors(
        self,
        exc_cls: type[BaseException],
    ) -> None:
        """Catastrophic interpreter errors must escape the ``Exception`` net."""

        class _CatastrophicStore:
            async def record(self, **_kwargs: object) -> None:
                return None

            async def query(self, **_kwargs: object) -> tuple[object, ...]:
                return ()

            async def summarize(self, **_kwargs: object) -> object:
                raise exc_cls

            async def count(self) -> int:
                return 0

            async def clear(self) -> None:
                return None

        agg = EvolutionSignalAggregator(_CatastrophicStore())  # type: ignore[arg-type]
        now = datetime.now(UTC)
        with pytest.raises(exc_cls):
            await agg.aggregate(
                since=now - timedelta(hours=1),
                until=now,
            )
