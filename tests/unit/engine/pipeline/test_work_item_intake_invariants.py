"""Unit tests for the WorkItem invariants that force one intake path.

Standing up an initiative is the operator's decision, taken in the charter
interview and recorded by their approval. These tests hold that the decision
cannot be reached any other way: a brief that forces decomposition must name
the approved charter that authorised it, so no classifier verdict and no
adapter can mint a project on its own.
"""

import pytest
from pydantic import ValidationError

from synthorg.engine.pipeline.models import WorkItem, WorkSource
from tests._shared import sid

pytestmark = pytest.mark.unit


def _work_item(**overrides: object) -> WorkItem:
    fields: dict[str, object] = {
        "origin_adapter_id": "harness",
        "source": WorkSource.OBJECTIVE,
        "title": "A goal",
        "raw_intent": "Do the thing.",
        "project": "proj",
        "requested_by": "operator",
    }
    fields.update(overrides)
    return WorkItem(**fields)  # type: ignore[arg-type]


def test_plan_required_without_a_charter_is_rejected() -> None:
    """A brief cannot stand up an initiative nobody approved."""
    with pytest.raises(ValidationError, match="approved charter"):
        _work_item(plan_required=True)


def test_plan_required_with_a_charter_is_accepted() -> None:
    """The charter dispatch path stays open."""
    charter_id = sid("charter-1")

    item = _work_item(plan_required=True, charter_id=charter_id)

    assert item.plan_required is True
    assert item.charter_id == charter_id


def test_an_ordinary_brief_needs_no_charter() -> None:
    """Only the initiative-forcing shape carries the requirement."""
    item = _work_item()

    assert item.plan_required is False
    assert item.charter_id is None


def test_a_leaf_forced_brief_needs_no_charter() -> None:
    """The integration stage mints a leaf, not an initiative."""
    item = _work_item(leaf_required=True)

    assert item.leaf_required is True
    assert item.charter_id is None


def test_both_forcing_flags_is_still_rejected() -> None:
    """The pre-existing mirror invariant is unaffected."""
    with pytest.raises(ValidationError, match="both a plan and a single leaf"):
        _work_item(plan_required=True, leaf_required=True, charter_id=sid("charter-2"))
