"""Tests for the deterministic memory write gate.

The gate is what stands between an agent's judgement about what mattered
and the store. Agents are unreliable precisely at contradiction (STALE:
76% at spotting an outdated belief under direct questioning, 4% when a
query presupposes it), so dedup and supersession are decided here rather
than left to the writer or to the retriever to notice later.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.memory.write_gate import (
    WriteDisposition,
    evaluate_write,
)

pytestmark = pytest.mark.unit


def _entry(
    content: str,
    *,
    entry_id: str = "mem-1",
    category: MemoryCategory = MemoryCategory.SEMANTIC,
) -> MemoryEntry:
    """Build a stored memory entry."""
    return MemoryEntry(
        id=NotBlankStr(entry_id),
        agent_id=NotBlankStr("agent-1"),
        category=category,
        content=NotBlankStr(content),
        metadata=MemoryMetadata(),
        created_at=datetime(2026, 7, 20, tzinfo=UTC),
    )


_LESSON = "Roll back the deploy before draining the connection pool."


class TestDeduplication:
    def test_identical_content_is_a_noop(self) -> None:
        decision = evaluate_write(_LESSON, existing=(_entry(_LESSON),))

        assert decision.disposition is WriteDisposition.NOOP
        assert decision.duplicate_of == "mem-1"

    def test_reworded_near_duplicate_is_a_noop(self) -> None:
        """Same fact, different words, must not accumulate twice."""
        stored = _entry("Roll back the deploy before draining connection pool.")

        decision = evaluate_write(_LESSON, existing=(stored,))

        assert decision.disposition is WriteDisposition.NOOP

    def test_unrelated_content_is_added(self) -> None:
        stored = _entry("Prefer the staging cluster for load tests.")

        decision = evaluate_write(_LESSON, existing=(stored,))

        assert decision.disposition is WriteDisposition.ADD
        assert decision.duplicate_of is None

    def test_empty_store_always_adds(self) -> None:
        assert evaluate_write(_LESSON, existing=()).disposition is WriteDisposition.ADD

    def test_dedup_reports_the_closest_match(self) -> None:
        near = _entry(_LESSON, entry_id="near")
        far = _entry("Something else entirely about billing.", entry_id="far")

        decision = evaluate_write(_LESSON, existing=(far, near))

        assert decision.duplicate_of == "near"

    def test_threshold_is_tunable(self) -> None:
        """The same partial overlap flips with the threshold."""
        stored = _entry("Roll back the deploy.")

        strict = evaluate_write(_LESSON, existing=(stored,))
        lenient = evaluate_write(_LESSON, existing=(stored,), dedup_threshold=0.5)

        assert strict.disposition is WriteDisposition.ADD
        assert lenient.disposition is WriteDisposition.NOOP

    def test_wording_only_differences_collapse(self) -> None:
        """Stop words alone must not make the same fact look new."""
        stored = _entry("Roll back the deploy before draining connection pool.")

        decision = evaluate_write(_LESSON, existing=(stored,))

        assert decision.disposition is WriteDisposition.NOOP


class TestSupersession:
    def test_declared_supersession_replaces_the_prior_entry(self) -> None:
        """Supersession is declared by the writer, never inferred.

        Inferring contradiction from text similarity is exactly what the
        literature shows models fail at, so the gate acts only on an
        explicit claim.
        """
        stored = _entry("Drain the pool before rolling back.", entry_id="old")

        decision = evaluate_write(
            _LESSON,
            existing=(stored,),
            supersedes=NotBlankStr("old"),
        )

        assert decision.disposition is WriteDisposition.SUPERSEDE
        assert decision.supersedes == "old"

    def test_supersession_wins_over_dedup(self) -> None:
        """An explicit replacement must land even if it reads as a duplicate."""
        stored = _entry(_LESSON, entry_id="old")

        decision = evaluate_write(
            _LESSON,
            existing=(stored,),
            supersedes=NotBlankStr("old"),
        )

        assert decision.disposition is WriteDisposition.SUPERSEDE

    def test_superseding_an_unknown_entry_is_rejected(self) -> None:
        """A dangling supersession link would orphan the claim."""
        decision = evaluate_write(
            _LESSON,
            existing=(_entry("unrelated", entry_id="other"),),
            supersedes=NotBlankStr("missing"),
        )

        assert decision.disposition is WriteDisposition.REJECT
        assert decision.reason is not None


class TestDeterminism:
    def test_same_inputs_give_the_same_decision(self) -> None:
        """No LLM, so the gate must be reproducible."""
        existing = (_entry("Prefer the staging cluster."),)

        first = evaluate_write(_LESSON, existing=existing)
        second = evaluate_write(_LESSON, existing=existing)

        assert first == second

    def test_blank_candidate_is_rejected(self) -> None:
        decision = evaluate_write("   ", existing=())

        assert decision.disposition is WriteDisposition.REJECT
