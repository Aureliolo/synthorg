"""Tests for the error signal aggregator."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.budget.coordination_config import ErrorCategory
from synthorg.engine.classification.models import (
    ClassificationResult,
    ErrorFinding,
    ErrorSeverity,
)
from synthorg.engine.classification.taxonomy_store import (
    InMemoryErrorTaxonomyStore,
)
from synthorg.meta.signals.errors import ErrorSignalAggregator


def _result(
    *findings: ErrorFinding,
    classified_at: datetime,
) -> ClassificationResult:
    cats = tuple({f.category for f in findings}) or (
        ErrorCategory.LOGICAL_CONTRADICTION,
    )
    return ClassificationResult(
        execution_id="exec",
        agent_id="agent",
        task_id="task",
        categories_checked=cats,
        findings=findings,
        classified_at=classified_at,
    )


@pytest.mark.unit
class TestErrorSignalAggregatorNoStore:
    """Aggregator returns empty summary when no store is wired."""

    async def test_no_store_yields_empty_summary(self) -> None:
        agg = ErrorSignalAggregator()
        now = datetime.now(UTC)
        summary = await agg.aggregate(
            since=now - timedelta(hours=1),
            until=now,
        )
        assert summary.total_findings == 0
        assert summary.categories == ()

    async def test_domain_is_errors(self) -> None:
        agg = ErrorSignalAggregator()
        assert agg.domain == "errors"


@pytest.mark.unit
class TestErrorSignalAggregatorWithStore:
    """Aggregator defers to the store's summarize method."""

    async def test_aggregates_real_findings(self) -> None:
        store = InMemoryErrorTaxonomyStore()
        now = datetime.now(UTC)
        ts = now - timedelta(minutes=15)
        await store.on_classification(
            _result(
                ErrorFinding(
                    category=ErrorCategory.LOGICAL_CONTRADICTION,
                    severity=ErrorSeverity.HIGH,
                    description="test",
                ),
                classified_at=ts,
            ),
        )
        agg = ErrorSignalAggregator(store)
        summary = await agg.aggregate(
            since=now - timedelta(hours=1),
            until=now,
        )
        assert summary.total_findings == 1
        assert summary.most_severe_category == ErrorCategory.LOGICAL_CONTRADICTION.value

    async def test_swallows_store_errors_returns_empty(self) -> None:
        """A failing store yields an empty summary, not an exception."""

        class _ExplodingStore:
            async def on_classification(self, result: object) -> None:
                return None

            async def query_findings(
                self,
                *,
                since: datetime,
                until: datetime,
            ) -> tuple[ErrorFinding, ...]:
                return ()

            async def summarize(self, *, since: datetime, until: datetime) -> object:
                msg = "boom"
                raise RuntimeError(msg)

            async def count(self) -> int:
                return 0

            async def clear(self) -> None:
                return None

        agg = ErrorSignalAggregator(_ExplodingStore())  # type: ignore[arg-type]
        now = datetime.now(UTC)
        summary = await agg.aggregate(
            since=now - timedelta(hours=1),
            until=now,
        )
        assert summary.total_findings == 0

    @pytest.mark.parametrize("exc_cls", [MemoryError, RecursionError])
    async def test_propagates_catastrophic_interpreter_errors(
        self,
        exc_cls: type[BaseException],
    ) -> None:
        """Catastrophic interpreter errors must escape the ``Exception`` net."""

        class _CatastrophicStore:
            async def on_classification(self, result: object) -> None:
                return None

            async def query_findings(
                self,
                *,
                since: datetime,
                until: datetime,
            ) -> tuple[ErrorFinding, ...]:
                return ()

            async def summarize(self, *, since: datetime, until: datetime) -> object:
                raise exc_cls

            async def count(self) -> int:
                return 0

            async def clear(self) -> None:
                return None

        agg = ErrorSignalAggregator(_CatastrophicStore())  # type: ignore[arg-type]
        now = datetime.now(UTC)
        with pytest.raises(exc_cls):
            await agg.aggregate(
                since=now - timedelta(hours=1),
                until=now,
            )
