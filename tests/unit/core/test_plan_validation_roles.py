# module-kind: tests
"""Which declared owner a plan item may carry, decided in one place.

Two questions meet here and they are not the same one. "Is this role staffed"
depends entirely on the roster. "May this role own work at all" does not depend
on it, and answering that one second is what let a judge through: a roster
derivation excludes gate roles, so an org whose active agents are all judges
derives an empty roster, and an empty roster is read as "no roster known" and
passes everything.
"""

import pytest

from synthorg.core.plan_role_validation import describe_unroutable_role
from synthorg.core.role_catalog import (
    COMPLETION_REVIEWER_ROLE_NAME,
    RED_TEAM_ROLE_NAME,
)
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit

_STAFFED = (NotBlankStr("Developer"), NotBlankStr("Designer"))


class TestAGateRoleIsNeverRoutable:
    """It judges finished work, so it cannot be the party that produced it."""

    @pytest.mark.parametrize(
        "role", [COMPLETION_REVIEWER_ROLE_NAME, RED_TEAM_ROLE_NAME]
    )
    def test_it_is_refused_even_when_the_roster_is_empty(self, role: str) -> None:
        """The staffing state that turned the roster filter into a fail-open.

        An org whose ACTIVE agents are all judges derives an empty roster, and
        the no-roster-known pass below would otherwise wave through every role,
        the judge included.
        """
        detail = describe_unroutable_role(
            entity_id="item-1", required_role=role, available_roles=()
        )

        assert detail is not None
        assert "judges finished work" in detail

    @pytest.mark.parametrize(
        "role", [COMPLETION_REVIEWER_ROLE_NAME, RED_TEAM_ROLE_NAME]
    )
    def test_it_is_refused_even_when_the_role_is_staffed(self, role: str) -> None:
        """Staffed is exactly what a gate role is, so membership cannot decide.

        This is also the path no roster derivation reaches: an operator editing
        a plan item's owner by hand supplies the role directly.
        """
        detail = describe_unroutable_role(
            entity_id="item-1",
            required_role=role,
            available_roles=(*_STAFFED, NotBlankStr(role)),
        )

        assert detail is not None
        assert "judges finished work" in detail

    def test_it_is_refused_however_an_operator_typed_it(self) -> None:
        """The catalogue's own normalisation decides, not the exact spelling."""
        detail = describe_unroutable_role(
            entity_id="item-1",
            required_role="  completion reviewer  ",
            available_roles=_STAFFED,
        )

        assert detail is not None


class TestAnOrdinaryRole:
    """Unchanged: staffed passes, invented is refused, unowned is not asked."""

    def test_a_staffed_role_routes(self) -> None:
        assert (
            describe_unroutable_role(
                entity_id="item-1",
                required_role="Developer",
                available_roles=_STAFFED,
            )
            is None
        )

    def test_an_invented_role_names_the_valid_set(self) -> None:
        detail = describe_unroutable_role(
            entity_id="item-1",
            required_role="Backend Engineer",
            available_roles=_STAFFED,
        )

        assert detail is not None
        assert "Designer, Developer" in detail

    def test_an_empty_roster_still_means_no_roster_known(self) -> None:
        """An org with no agents has nothing to check a plain role against."""
        assert (
            describe_unroutable_role(
                entity_id="item-1", required_role="Developer", available_roles=()
            )
            is None
        )

    def test_an_unowned_item_is_not_asked_about(self) -> None:
        assert (
            describe_unroutable_role(
                entity_id="item-1", required_role=None, available_roles=_STAFFED
            )
            is None
        )
