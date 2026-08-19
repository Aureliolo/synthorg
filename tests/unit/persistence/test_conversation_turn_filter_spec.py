"""What the conversation-turn filter spec refuses to ask.

``conversation_id`` and ``conversation_ids`` ask the same question of the same
column. Both translate to an independent clause, so a spec carrying the two
would AND into their intersection on either backend; what makes it wrong is
that no caller means an intersection, so the spec that expresses it is a
mistake to catch at the boundary. ``sequence`` narrows within an id predicate
and is refused alone, because the index is keyed on the conversation first and
a sequence by itself scans every turn ever written. Both are model invariants,
so they are asserted here rather than against a backend.
"""

import pytest
from pydantic import ValidationError

from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import MAX_PAGE_SIZE
from synthorg.persistence.conversation_protocol import ConversationTurnFilterSpec

pytestmark = pytest.mark.unit


def test_both_id_predicates_together_are_refused() -> None:
    with pytest.raises(ValidationError):
        ConversationTurnFilterSpec(
            conversation_id=NotBlankStr("conv-a"),
            conversation_ids=(NotBlankStr("conv-b"),),
        )


def test_either_id_predicate_alone_is_accepted() -> None:
    assert (
        ConversationTurnFilterSpec(
            conversation_id=NotBlankStr("conv-a")
        ).conversation_ids
        is None
    )
    assert (
        ConversationTurnFilterSpec(
            conversation_ids=(NotBlankStr("conv-a"),)
        ).conversation_id
        is None
    )


def test_an_empty_id_set_is_a_predicate_not_an_absence() -> None:
    # The empty tuple must survive construction distinct from ``None``: it
    # means "none of them", which the backends write as a false predicate,
    # while ``None`` means "do not filter on this column".
    assert ConversationTurnFilterSpec(conversation_ids=()).conversation_ids == ()


def test_a_negative_sequence_is_refused() -> None:
    with pytest.raises(ValidationError):
        ConversationTurnFilterSpec(sequence=-1)


def test_a_sequence_with_no_conversation_predicate_is_refused() -> None:
    # Measured as a full scan on both backends. Nothing asks for it, and the
    # caller that wants every conversation's opener is asking for a different
    # query, which it should have to say.
    with pytest.raises(ValidationError):
        ConversationTurnFilterSpec(sequence=0)


def test_a_sequence_narrowing_either_id_predicate_is_accepted() -> None:
    assert (
        ConversationTurnFilterSpec(
            conversation_id=NotBlankStr("conv-a"), sequence=0
        ).sequence
        == 0
    )
    assert (
        ConversationTurnFilterSpec(
            conversation_ids=(NotBlankStr("conv-a"),), sequence=0
        ).sequence
        == 0
    )


def test_a_sequence_narrowing_an_empty_id_set_is_accepted() -> None:
    # "None of them" is a predicate, so it satisfies the requirement: the
    # query is bounded to nothing, which is the cheapest scan there is.
    assert ConversationTurnFilterSpec(conversation_ids=(), sequence=0).sequence == 0


def test_a_batch_larger_than_one_page_is_refused() -> None:
    # A batch is answered by ONE page, so a caller naming more conversations
    # than a page returns gets a silently short answer and renders the
    # overflow as though those conversations had no turns.
    with pytest.raises(ValidationError):
        ConversationTurnFilterSpec(
            conversation_ids=tuple(
                NotBlankStr(f"conv-{n}") for n in range(MAX_PAGE_SIZE + 1)
            )
        )


def test_a_batch_of_exactly_one_page_is_accepted() -> None:
    spec = ConversationTurnFilterSpec(
        conversation_ids=tuple(NotBlankStr(f"conv-{n}") for n in range(MAX_PAGE_SIZE))
    )
    assert spec.conversation_ids is not None
    assert len(spec.conversation_ids) == MAX_PAGE_SIZE
