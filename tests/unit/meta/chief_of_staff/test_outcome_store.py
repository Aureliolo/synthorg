"""Unit tests for MemoryBackendOutcomeStore."""

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.memory.backends.inmemory.adapter import InMemoryBackend
from synthorg.meta.chief_of_staff.models import ProposalOutcome
from synthorg.meta.chief_of_staff.outcome_store import MemoryBackendOutcomeStore
from synthorg.meta.models import ProposalAltitude

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 4, 15, 12, 0, 0, tzinfo=UTC)
_AGENT_ID = NotBlankStr("chief-of-staff")


def _make_outcome(
    *,
    decision: Literal["approved", "rejected"] = "approved",
    rule: str = "quality_declining",
    altitude: ProposalAltitude = ProposalAltitude.CONFIG_TUNING,
    confidence: float = 0.7,
) -> ProposalOutcome:
    return ProposalOutcome(
        proposal_id=uuid4(),
        title="Test proposal",
        altitude=altitude,
        source_rule=rule,
        decision=decision,
        confidence_at_decision=confidence,
        decided_at=_NOW,
        decided_by="reviewer",
    )


async def _connected_backend() -> InMemoryBackend:
    backend = InMemoryBackend()
    await backend.connect()
    return backend


class TestRecordOutcome:
    """MemoryBackendOutcomeStore.record_outcome tests."""

    async def test_returns_memory_id(self) -> None:
        backend = await _connected_backend()
        store = MemoryBackendOutcomeStore(
            backend=backend,
            agent_id=_AGENT_ID,
        )
        outcome = _make_outcome()
        memory_id = await store.record_outcome(outcome)
        assert len(memory_id) > 0

    async def test_round_trip(self) -> None:
        backend = await _connected_backend()
        store = MemoryBackendOutcomeStore(
            backend=backend,
            agent_id=_AGENT_ID,
        )
        outcome = _make_outcome(rule="budget_overrun", decision="rejected")
        await store.record_outcome(outcome)
        results = await store.recent_outcomes(
            rule_name=NotBlankStr("budget_overrun"),
        )
        assert len(results) == 1
        assert results[0].decision == "rejected"
        assert results[0].source_rule == "budget_overrun"

    @pytest.mark.parametrize("exc_cls", [MemoryError, RecursionError])
    async def test_record_outcome_propagates_catastrophic_errors(
        self,
        exc_cls: type[BaseException],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``MemoryError`` / ``RecursionError`` from the backend escape
        the broad ``except Exception`` redacted-logging handler.

        ``record_outcome`` logs application errors via
        ``log_exception_redacted`` and re-raises; catastrophic interpreter
        state must bypass that helper entirely so the surrounding event
        loop sees the failure without any additional log-handler work
        (which may itself recurse or allocate).
        """
        backend = await _connected_backend()

        async def _raising_store(*_args: object, **_kwargs: object) -> str:
            raise exc_cls

        monkeypatch.setattr(backend, "store", _raising_store)
        store = MemoryBackendOutcomeStore(
            backend=backend,
            agent_id=_AGENT_ID,
        )
        with pytest.raises(exc_cls):
            await store.record_outcome(_make_outcome())


class TestGetStats:
    """MemoryBackendOutcomeStore.get_stats tests."""

    async def test_returns_none_below_min_outcomes(self) -> None:
        backend = await _connected_backend()
        store = MemoryBackendOutcomeStore(
            backend=backend,
            agent_id=_AGENT_ID,
            min_outcomes=3,
        )
        await store.record_outcome(_make_outcome())
        await store.record_outcome(_make_outcome())
        stats = await store.get_stats(
            NotBlankStr("quality_declining"),
            ProposalAltitude.CONFIG_TUNING,
        )
        assert stats is None

    async def test_returns_stats_at_min_outcomes(self) -> None:
        backend = await _connected_backend()
        store = MemoryBackendOutcomeStore(
            backend=backend,
            agent_id=_AGENT_ID,
            min_outcomes=3,
        )
        for _ in range(3):
            await store.record_outcome(_make_outcome(decision="approved"))
        stats = await store.get_stats(
            NotBlankStr("quality_declining"),
            ProposalAltitude.CONFIG_TUNING,
        )
        assert stats is not None
        assert stats.total_proposals == 3
        assert stats.approved_count == 3
        assert stats.approval_rate == pytest.approx(1.0)

    async def test_mixed_decisions(self) -> None:
        backend = await _connected_backend()
        store = MemoryBackendOutcomeStore(
            backend=backend,
            agent_id=_AGENT_ID,
            min_outcomes=1,
        )
        await store.record_outcome(_make_outcome(decision="approved"))
        await store.record_outcome(_make_outcome(decision="approved"))
        await store.record_outcome(_make_outcome(decision="rejected"))
        stats = await store.get_stats(
            NotBlankStr("quality_declining"),
            ProposalAltitude.CONFIG_TUNING,
        )
        assert stats is not None
        assert stats.approved_count == 2
        assert stats.rejected_count == 1
        assert stats.approval_rate == pytest.approx(2 / 3)

    async def test_filters_by_altitude(self) -> None:
        backend = await _connected_backend()
        store = MemoryBackendOutcomeStore(
            backend=backend,
            agent_id=_AGENT_ID,
            min_outcomes=1,
        )
        await store.record_outcome(
            _make_outcome(altitude=ProposalAltitude.CONFIG_TUNING),
        )
        await store.record_outcome(
            _make_outcome(altitude=ProposalAltitude.ARCHITECTURE),
        )
        stats = await store.get_stats(
            NotBlankStr("quality_declining"),
            ProposalAltitude.ARCHITECTURE,
        )
        assert stats is not None
        assert stats.total_proposals == 1

    async def test_empty_store(self) -> None:
        backend = await _connected_backend()
        store = MemoryBackendOutcomeStore(
            backend=backend,
            agent_id=_AGENT_ID,
        )
        stats = await store.get_stats(
            NotBlankStr("nonexistent"),
            ProposalAltitude.CONFIG_TUNING,
        )
        assert stats is None

    async def test_corrupted_entries_skipped(self) -> None:
        """Deserialization failures are skipped; valid entries counted."""
        backend = await _connected_backend()
        store = MemoryBackendOutcomeStore(
            backend=backend,
            agent_id=_AGENT_ID,
            min_outcomes=1,
        )
        # Record 3 valid outcomes.
        for _ in range(3):
            await store.record_outcome(_make_outcome(decision="approved"))
        # Inject a corrupted entry directly via the backend.
        from synthorg.core.enums import MemoryCategory
        from synthorg.memory.models import MemoryMetadata, MemoryStoreRequest

        await backend.store(
            _AGENT_ID,
            MemoryStoreRequest(
                category=MemoryCategory.EPISODIC,
                namespace="chief_of_staff",
                content="NOT VALID JSON",
                metadata=MemoryMetadata(
                    tags=(
                        NotBlankStr("rule:quality_declining"),
                        NotBlankStr("altitude:config_tuning"),
                    ),
                ),
            ),
        )
        stats = await store.get_stats(
            NotBlankStr("quality_declining"),
            ProposalAltitude.CONFIG_TUNING,
        )
        # Only the 3 valid entries should be counted.
        assert stats is not None
        assert stats.total_proposals == 3
        assert stats.approved_count == 3

    async def test_all_corrupted_returns_none(self) -> None:
        """If all entries fail deserialization, returns None."""
        backend = await _connected_backend()
        store = MemoryBackendOutcomeStore(
            backend=backend,
            agent_id=_AGENT_ID,
            min_outcomes=1,
        )
        from synthorg.core.enums import MemoryCategory
        from synthorg.memory.models import MemoryMetadata, MemoryStoreRequest

        await backend.store(
            _AGENT_ID,
            MemoryStoreRequest(
                category=MemoryCategory.EPISODIC,
                namespace="chief_of_staff",
                content="CORRUPT DATA",
                metadata=MemoryMetadata(
                    tags=(
                        NotBlankStr("rule:quality_declining"),
                        NotBlankStr("altitude:config_tuning"),
                    ),
                ),
            ),
        )
        stats = await store.get_stats(
            NotBlankStr("quality_declining"),
            ProposalAltitude.CONFIG_TUNING,
        )
        assert stats is None


class TestRecentOutcomes:
    """MemoryBackendOutcomeStore.recent_outcomes tests."""

    async def test_empty_store(self) -> None:
        backend = await _connected_backend()
        store = MemoryBackendOutcomeStore(
            backend=backend,
            agent_id=_AGENT_ID,
        )
        results = await store.recent_outcomes()
        assert results == ()

    async def test_respects_limit(self) -> None:
        backend = await _connected_backend()
        store = MemoryBackendOutcomeStore(
            backend=backend,
            agent_id=_AGENT_ID,
        )
        for _ in range(5):
            await store.record_outcome(_make_outcome())
        results = await store.recent_outcomes(limit=2)
        assert len(results) == 2

    async def test_filters_by_rule(self) -> None:
        backend = await _connected_backend()
        store = MemoryBackendOutcomeStore(
            backend=backend,
            agent_id=_AGENT_ID,
        )
        await store.record_outcome(_make_outcome(rule="rule_a"))
        await store.record_outcome(_make_outcome(rule="rule_b"))
        results = await store.recent_outcomes(
            rule_name=NotBlankStr("rule_a"),
        )
        assert len(results) == 1
        assert results[0].source_rule == "rule_a"
