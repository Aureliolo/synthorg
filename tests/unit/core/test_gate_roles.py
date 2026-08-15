"""Tests for the gate-role predicate.

Holding a gate role confers judging authority over finished work, so the
predicate decides who the singleton hire guard covers, which roster changes
wake the staffing sweep, and which role grants the agent MCP surface must
refuse. It is a property of the role an operator can see in the roster,
never a flag conferred invisibly.
"""

import pytest

from synthorg.core.role_catalog import (
    BUILTIN_ROLES,
    COMPLETION_REVIEWER_ROLE_NAME,
    GATE_ROLE_NAMES,
    RED_TEAM_ROLE_NAME,
    role_is_gate_role,
)

pytestmark = pytest.mark.unit


class TestRoleIsGateRole:
    """Only the two catalogued roles judge finished work."""

    def test_completion_reviewer_is_a_gate_role(self) -> None:
        assert role_is_gate_role(COMPLETION_REVIEWER_ROLE_NAME)

    def test_red_team_is_a_gate_role(self) -> None:
        assert role_is_gate_role(RED_TEAM_ROLE_NAME)

    def test_it_is_case_and_whitespace_insensitive(self) -> None:
        # An operator types the role by hand through the roster surface, so
        # the answer must not hinge on exact casing.
        assert role_is_gate_role("  completion REVIEWER ")

    def test_every_other_builtin_role_is_ordinary(self) -> None:
        for role in BUILTIN_ROLES:
            if role.name in {COMPLETION_REVIEWER_ROLE_NAME, RED_TEAM_ROLE_NAME}:
                continue
            assert not role_is_gate_role(role.name), role.name

    def test_unknown_role_is_ordinary(self) -> None:
        assert not role_is_gate_role("Backend Developer Deluxe")

    def test_blank_role_is_ordinary(self) -> None:
        assert not role_is_gate_role("")

    def test_gate_role_names_holds_exactly_the_two_gate_roles(self) -> None:
        assert len(GATE_ROLE_NAMES) == 2
