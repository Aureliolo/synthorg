"""Unit tests for the shared WHERE-clause builders.

The dual-backend conformance suite exercises these builders end-to-end, but
they also carry the only genuinely backend-specific bits (placeholder token,
empty-filter fallback, enum ``.value`` coercion, and the evolution-outcome
serialiser callbacks), so those are pinned directly here.
"""

from datetime import UTC, datetime

import pytest

from synthorg.meta.chief_of_staff.enums import ConversationInviteStatus
from synthorg.persistence._shared._filter_clauses import (
    build_conversation_invite_filter_clauses,
    build_deliverable_receipt_filter_clauses,
    build_evolution_outcome_filter_clauses,
)
from synthorg.persistence.conversation_invite_protocol import (
    ConversationInviteFilterSpec,
)
from synthorg.persistence.deliverable_receipt_protocol import (
    DeliverableReceiptFilterSpec,
)
from synthorg.persistence.evolution_outcome_protocol import EvolutionOutcomeFilterSpec


@pytest.mark.unit
@pytest.mark.parametrize(("placeholder", "empty"), [("?", "1=1"), ("%s", "TRUE")])
def test_empty_filter_falls_back_per_backend(placeholder: str, empty: str) -> None:
    body, params = build_conversation_invite_filter_clauses(
        ConversationInviteFilterSpec(),
        placeholder=placeholder,
        empty=empty,
    )
    assert body == empty
    assert params == []


@pytest.mark.unit
@pytest.mark.parametrize("placeholder", ["?", "%s"])
def test_enum_field_serialises_to_value_with_placeholder(placeholder: str) -> None:
    body, params = build_conversation_invite_filter_clauses(
        ConversationInviteFilterSpec(
            status=ConversationInviteStatus.PENDING,
        ),
        placeholder=placeholder,
        empty="1=1",
    )
    assert body == f"status = {placeholder}"
    # The enum is reduced to its ``.value`` string, never the member itself.
    assert params == ["pending"]


@pytest.mark.unit
def test_deliverable_receipt_seeds_mandatory_project_id() -> None:
    body, params = build_deliverable_receipt_filter_clauses(
        DeliverableReceiptFilterSpec(project_id="proj-1", task_id="task-1"),
        placeholder="?",
    )
    assert body == "project_id = ? AND task_id = ?"
    assert params == ["proj-1", "task-1"]


@pytest.mark.unit
def test_evolution_outcome_applies_serialiser_callbacks() -> None:
    since = datetime(2026, 1, 1, tzinfo=UTC)
    clauses, params = build_evolution_outcome_filter_clauses(
        EvolutionOutcomeFilterSpec(applied=True, since=since),
        placeholder="?",
        serialize_applied=int,
        serialize_timestamp=lambda ts: ts.isoformat(),
    )
    # Raw clause list (the callers join it themselves), and the callbacks
    # carry the backend-specific value handling (bool -> int, dt -> ISO).
    assert clauses == ["applied = ?", "recorded_at >= ?"]
    assert params == [1, since.isoformat()]
