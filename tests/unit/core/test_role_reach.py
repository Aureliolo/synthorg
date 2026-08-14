"""Tests for the gate roles' cross-project reach.

A working agent is confined to the projects it is staffed on; a quality
gate judges work across the org and is not. That distinction used to live
on a hidden ``AgentIdentity.is_system`` flag; it now follows from the role
an operator can see in the roster.
"""

import pytest

from synthorg.core.role_catalog import (
    BUILTIN_ROLES,
    COMPLETION_REVIEWER_ROLE_NAME,
    GATE_ROLE_NAMES,
    RED_TEAM_ROLE_NAME,
    role_reaches_every_project,
)

pytestmark = pytest.mark.unit


class TestRoleReachesEveryProject:
    """Only the two catalogued gate roles reach beyond their project team."""

    def test_completion_reviewer_reaches_every_project(self) -> None:
        assert role_reaches_every_project(COMPLETION_REVIEWER_ROLE_NAME)

    def test_red_team_reaches_every_project(self) -> None:
        assert role_reaches_every_project(RED_TEAM_ROLE_NAME)

    def test_reach_is_case_and_whitespace_insensitive(self) -> None:
        # An operator types the role by hand through the roster surface, so
        # the reach must not hinge on exact casing.
        assert role_reaches_every_project("  completion REVIEWER ")

    def test_every_other_builtin_role_is_confined(self) -> None:
        for role in BUILTIN_ROLES:
            if role.name in {COMPLETION_REVIEWER_ROLE_NAME, RED_TEAM_ROLE_NAME}:
                continue
            assert not role_reaches_every_project(role.name), role.name

    def test_unknown_role_is_confined(self) -> None:
        assert not role_reaches_every_project("Backend Developer Deluxe")

    def test_blank_role_is_confined(self) -> None:
        assert not role_reaches_every_project("")

    def test_gate_role_names_holds_exactly_the_two_gate_roles(self) -> None:
        assert len(GATE_ROLE_NAMES) == 2
