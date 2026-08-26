# module-kind: tests
"""What an operator may claim about a plan item, and what is refused.

``satisfies`` has two writers: the planner's parse and the item list an
operator submits. Refusing an invented claim at one of them leaves the other
free to write a claim that reads as coverage on every surface showing the
field and is coverage to none of them, so both boundaries are covered here.
"""

import pytest

from synthorg.api.controllers._plan_input_validation import reject_unnamed_claims
from synthorg.core.domain_errors import ValidationError
from synthorg.core.plan import PlanItem
from synthorg.core.types import NotBlankStr
from tests._shared import sid

pytestmark = pytest.mark.unit

_OBJECTIVE: tuple[NotBlankStr, ...] = (
    NotBlankStr("A player can play a full game"),
    NotBlankStr("The board renders at sixty frames a second"),
)


def _item(label: str, *, satisfies: tuple[str, ...] = ()) -> PlanItem:
    """Build one work item claiming *satisfies*.

    Returns:
        The item.
    """
    return PlanItem(
        id=NotBlankStr(sid(label)),
        title=NotBlankStr(f"Item {label}"),
        description=NotBlankStr(f"Build {label}"),
        acceptance_criteria=(NotBlankStr(f"{label} works"),),
        expected_artifacts=(NotBlankStr(f"src/{label}.py"),),
        satisfies=tuple(NotBlankStr(one) for one in satisfies),
    )


class TestAClaimMustNameSomething:
    def test_an_invented_claim_is_refused(self) -> None:
        items = (_item("alpha", satisfies=("it feels good to play",)),)

        with pytest.raises(ValidationError) as caught:
            reject_unnamed_claims(items, _OBJECTIVE)

        assert "it feels good to play" in str(caught.value)

    def test_the_refusal_quotes_every_criterion_to_copy_from(self) -> None:
        """All of them: a partial list is one the operator cannot copy from."""
        items = (_item("alpha", satisfies=("it feels good to play",)),)

        with pytest.raises(ValidationError) as caught:
            reject_unnamed_claims(items, _OBJECTIVE)

        detail = str(caught.value)
        assert all(str(criterion) in detail for criterion in _OBJECTIVE)

    def test_a_claim_naming_a_criterion_is_accepted(self) -> None:
        items = (_item("alpha", satisfies=(str(_OBJECTIVE[0]),)),)

        reject_unnamed_claims(items, _OBJECTIVE)

    def test_a_claim_differing_only_in_case_and_spacing_is_accepted(self) -> None:
        """Forgiving about spelling, unforgiving about content, at the wire."""
        items = (_item("alpha", satisfies=("  A PLAYER   can play a full game ",)),)

        reject_unnamed_claims(items, _OBJECTIVE)

    def test_an_item_claiming_nothing_is_pure_support(self) -> None:
        reject_unnamed_claims((_item("alpha"),), _OBJECTIVE)

    def test_a_claim_is_refused_when_the_plan_states_no_criteria(self) -> None:
        """A plan answerable for nothing admits no claim.

        Not an exemption but the strictest case: it is what a subtree below a
        unit that claimed nothing inherits, and admitting everything there is
        what left one unchecked.
        """
        with pytest.raises(ValidationError):
            reject_unnamed_claims((_item("alpha", satisfies=("anything",)),), ())

    def test_a_plan_claiming_nothing_against_no_criteria_is_accepted(self) -> None:
        reject_unnamed_claims((_item("alpha"),), ())

    def test_every_offending_item_is_reported_at_once(self) -> None:
        """A revision is edited as a whole, so one per attempt is a round trip."""
        items = (
            _item("alpha", satisfies=("invented",)),
            _item("beta", satisfies=(str(_OBJECTIVE[1]),)),
            _item("gamma", satisfies=("also invented",)),
        )

        with pytest.raises(ValidationError) as caught:
            reject_unnamed_claims(items, _OBJECTIVE)

        detail = str(caught.value)
        assert "Item alpha" in detail
        assert "Item gamma" in detail
