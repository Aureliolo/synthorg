"""Mutual exclusion on the conversation-turn filter spec's id predicates.

``conversation_id`` and ``conversation_ids`` ask the same question of the same
column. A spec carrying both is two different questions about one column, and
whichever clause a backend happened to build first would silently be the
answer, differently per backend. The refusal is a model invariant, so it is
asserted here rather than against a backend.
"""

import pytest
from pydantic import ValidationError

from synthorg.core.types import NotBlankStr
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
