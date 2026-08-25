"""What an operator may say about a plan's shape, and what is refused.

The edit endpoints accept a whole item list, so a hand-authored or
hand-corrected tree arrives here before anything dispatches from it. Refusing
a malformed one at the boundary is what keeps the dispatch layer able to
assume the shape it walks.
"""

import pytest

from synthorg.api.controllers._plan_input_validation import reject_malformed_tree
from synthorg.api.controllers._plan_translation import item_from_payload
from synthorg.api.dto_plans import PlanItemPayload
from synthorg.core.domain_errors import ValidationError
from synthorg.core.plan import PlanItem, PlanOption
from synthorg.core.plan_enums import PlanItemKind
from synthorg.core.types import NotBlankStr
from tests._shared import sid

pytestmark = pytest.mark.unit


def _item(
    label: str,
    *,
    parent: str | None = None,
    kind: PlanItemKind = PlanItemKind.WORK,
    dependencies: tuple[str, ...] = (),
) -> PlanItem:
    """Build one plan item, bypassing the whole-plan tree validator.

    Returns:
        The item.
    """
    return PlanItem(
        id=NotBlankStr(sid(label)),
        title=NotBlankStr(f"Item {label}"),
        description=NotBlankStr(f"Build {label}"),
        parent_id=None if parent is None else NotBlankStr(sid(parent)),
        kind=kind,
        dependencies=tuple(NotBlankStr(sid(d)) for d in dependencies),
        acceptance_criteria=(NotBlankStr(f"{label} works"),),
        expected_artifacts=(NotBlankStr(f"src/{label}.py"),),
    )


def _decision(label: str) -> PlanItem:
    """Build a decision item, which offers options rather than deliverables.

    Returns:
        The item.
    """
    return PlanItem(
        id=NotBlankStr(sid(label)),
        title=NotBlankStr(f"Decide {label}"),
        description=NotBlankStr(f"Choose for {label}"),
        kind=PlanItemKind.DECISION,
        acceptance_criteria=(NotBlankStr("the choice is recorded"),),
        options=(
            PlanOption(
                id=NotBlankStr("a"),
                title=NotBlankStr("A"),
                summary=NotBlankStr("One way"),
                recommended=True,
            ),
            PlanOption(
                id=NotBlankStr("b"),
                title=NotBlankStr("B"),
                summary=NotBlankStr("Another way"),
            ),
        ),
    )


def _payload(**overrides: object) -> PlanItemPayload:
    """Build an edit payload for one work item.

    Returns:
        The payload.
    """
    fields: dict[str, object] = {
        "id": sid("a"),
        "title": "Item a",
        "description": "Build a",
        "acceptance_criteria": ["a works"],
        "expected_artifacts": ["src/a.py"],
    }
    fields.update(overrides)
    return PlanItemPayload.model_validate(fields)


class TestTheTreeAnOperatorMaySubmit:
    def test_a_well_formed_tree_is_accepted(self) -> None:
        reject_malformed_tree(
            (_item("engine"), _item("board", parent="engine"), _item("ui"))
        )

    def test_a_parent_the_plan_does_not_hold_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="not an item of this plan"):
            reject_malformed_tree((_item("board", parent="ghost"),))

    def test_a_containment_cycle_is_refused(self) -> None:
        # Nothing in it reaches a workstream, so no dispatch order exists and
        # the assembly walk would never terminate.
        with pytest.raises(ValidationError, match="containment cycle"):
            reject_malformed_tree((_item("a", parent="b"), _item("b", parent="a")))

    def test_hanging_work_off_a_decision_is_refused(self) -> None:
        # A decision is chosen rather than decomposed: dispatch strips it, so
        # its children would be orphaned the moment the plan ran.
        with pytest.raises(ValidationError, match="a decision is chosen"):
            reject_malformed_tree((_decision("pick"), _item("board", parent="pick")))

    def test_a_dependency_across_levels_is_refused(self) -> None:
        # A level is the unit a dependency is declared within, which was true
        # before the tree existed. A cross-subtree need is stated between the
        # containers, which the tree already expresses.
        with pytest.raises(ValidationError, match="same level"):
            reject_malformed_tree(
                (
                    _item("engine"),
                    _item("board", parent="engine"),
                    _item("ui", dependencies=("board",)),
                )
            )

    def test_every_violation_is_reported_rather_than_the_first(self) -> None:
        # A hand-authored plan is corrected in one pass, and reporting one
        # fault per submission costs a round per fault.
        with pytest.raises(ValidationError) as caught:
            reject_malformed_tree(
                (_item("a", parent="ghost"), _item("b", parent="phantom"))
            )
        detail = str(caught.value)
        assert sid("a") in detail
        assert sid("b") in detail


class TestWhatTheEditPayloadCarries:
    def test_the_parent_link_round_trips(self) -> None:
        item = item_from_payload(_payload(id=sid("board"), parent_id=sid("engine")))
        assert item.parent_id == sid("engine")

    def test_an_item_may_be_promoted_to_a_workstream(self) -> None:
        assert item_from_payload(_payload(parent_id=None)).parent_id is None

    def test_an_item_cannot_be_its_own_parent(self) -> None:
        with pytest.raises(ValueError, match="cannot be its own parent"):
            _payload(parent_id=sid("a"))

    def test_the_unsplit_note_is_dropped_by_an_edit(self) -> None:
        # The operator has just revised the item, so a note about the version
        # they replaced describes nothing. It is absent from the payload
        # entirely rather than carried and ignored.
        with pytest.raises(ValueError, match="unsplit_reason"):
            _payload(unsplit_reason="was still oversized")

    def test_an_edited_item_carries_no_note_forward(self) -> None:
        assert item_from_payload(_payload()).unsplit_reason is None
