"""Tests for the reviewed-work capability requirement.

Capability follows the TASK: the requirement is read off the work being
judged, so it decides WHO reviews rather than rewriting what anybody runs.
"""

import pytest

from synthorg.core.task_enums import Complexity, Stakes
from synthorg.engine.routing_policy.capability_ladder import required_capability_for

pytestmark = pytest.mark.unit


class TestRequiredCapabilityFor:
    """Stakes set the floor; substantial complexity raises it one rung."""

    @pytest.mark.parametrize(
        ("stakes", "expected"),
        [
            (Stakes.LOW, "basic"),
            (Stakes.NORMAL, "capable"),
            (Stakes.HIGH, "expert"),
            (Stakes.CRITICAL, "expert"),
        ],
    )
    def test_simple_work_takes_the_stakes_floor(
        self, stakes: Stakes, expected: str
    ) -> None:
        assert required_capability_for(stakes, Complexity.SIMPLE) == expected

    @pytest.mark.parametrize(
        ("complexity", "expected"),
        [
            (Complexity.SIMPLE, "basic"),
            (Complexity.MEDIUM, "basic"),
            (Complexity.COMPLEX, "capable"),
            (Complexity.EPIC, "capable"),
        ],
    )
    def test_complexity_raises_the_low_stakes_floor(
        self, complexity: Complexity, expected: str
    ) -> None:
        assert required_capability_for(Stakes.LOW, complexity) == expected

    def test_the_bump_saturates_at_the_strongest_rung(self) -> None:
        # Already at the top: an EPIC critical task cannot ask for more than
        # the ladder has.
        assert required_capability_for(Stakes.CRITICAL, Complexity.EPIC) == "expert"

    def test_normal_stakes_epic_work_needs_an_expert(self) -> None:
        assert required_capability_for(Stakes.NORMAL, Complexity.EPIC) == "expert"
