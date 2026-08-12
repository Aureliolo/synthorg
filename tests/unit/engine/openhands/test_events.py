# module-kind: tests
"""Which kinds of normalized OpenHands event may carry which fields.

The invariants exist because the adapter's accounting reads these fields
positionally: a `tool_name` on an event that is not a call, or token figures on
an event that is not a turn, both read downstream as real measurements. The
mapping from the SDK's concrete classes is the only writer, so a stray value is
a mapping bug, and it is worth failing at construction rather than surfacing as
a turn count nobody can reconcile.
"""

import pytest
from pydantic import ValidationError

from synthorg.core.types import NotBlankStr
from synthorg.engine.openhands.events import OpenHandsEvent, OpenHandsEventKind

pytestmark = pytest.mark.unit

_NON_TOOL_KINDS = (
    OpenHandsEventKind.MESSAGE,
    OpenHandsEventKind.OBSERVATION,
    OpenHandsEventKind.FINISHED,
    OpenHandsEventKind.ERROR,
)
_NON_TURN_KINDS = (
    OpenHandsEventKind.OBSERVATION,
    OpenHandsEventKind.TOOL_ERROR,
    OpenHandsEventKind.FINISHED,
    OpenHandsEventKind.ERROR,
)


class TestToolName:
    @pytest.mark.parametrize(
        "kind", [OpenHandsEventKind.ACTION, OpenHandsEventKind.TOOL_ERROR]
    )
    def test_a_kind_that_names_a_tool_may_carry_one(
        self, kind: OpenHandsEventKind
    ) -> None:
        # TOOL_ERROR is the rejection of a specific call, so the name it
        # rejected is what makes the event actionable to the next turn.
        event = OpenHandsEvent(kind=kind, tool_name=NotBlankStr("read_file"))

        assert event.tool_name == "read_file"

    @pytest.mark.parametrize("kind", _NON_TOOL_KINDS)
    def test_any_other_kind_is_rejected(self, kind: OpenHandsEventKind) -> None:
        with pytest.raises(ValidationError, match="only valid on an ACTION"):
            OpenHandsEvent(kind=kind, tool_name=NotBlankStr("read_file"))


class TestTurnFigures:
    @pytest.mark.parametrize(
        "kind", [OpenHandsEventKind.MESSAGE, OpenHandsEventKind.ACTION]
    )
    def test_a_turn_may_carry_token_and_cost_figures(
        self, kind: OpenHandsEventKind
    ) -> None:
        event = OpenHandsEvent(kind=kind, input_tokens=120, output_tokens=34, cost=0.5)

        assert (event.input_tokens, event.output_tokens, event.cost) == (120, 34, 0.5)

    @pytest.mark.parametrize("kind", _NON_TURN_KINDS)
    @pytest.mark.parametrize(
        ("input_tokens", "output_tokens", "cost"),
        [(1, 0, 0.0), (0, 1, 0.0), (0, 0, 0.01)],
    )
    def test_a_non_turn_carrying_any_figure_is_rejected(
        self,
        kind: OpenHandsEventKind,
        input_tokens: int,
        output_tokens: int,
        cost: float,
    ) -> None:
        # Each figure is asserted on its own: a guard that dropped one of the
        # three would still pass a test that always set all three at once.
        with pytest.raises(ValidationError, match="only valid on a turn"):
            OpenHandsEvent(
                kind=kind,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost=cost,
            )
