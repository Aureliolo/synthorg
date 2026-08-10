"""Tests for the confined-auto-approval gate.

A preset grants bare categories, so its auto-approved set is whatever the
taxonomy holds when the resolver expands it. Two widenings reached main that
way: granting ``"docs"`` also auto-approved a billed external image provider,
because the design category defaulted to ``docs:write``; and SEMI granted the
bare ``"vcs"``, which expands to ``vcs:push``.

These cover the shape the gate must reject, the shape it must accept, the
``"all"`` grant it must not judge, and the shipped presets.
"""

import pytest
from scripts.check_autonomy_auto_approve_confined import _expand, main, scan_presets

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.security.action_types import (
    WORKTREE_CONFINED_ACTION_TYPES,
    ActionTypeRegistry,
)
from synthorg.security.autonomy.enums import ActionType
from synthorg.security.autonomy.models import BUILTIN_PRESETS, AutonomyPreset

pytestmark = pytest.mark.unit


def _preset(*auto_approve: str) -> dict[str, AutonomyPreset]:
    """Wrap *auto_approve* in a one-entry preset mapping for the scanner.

    Returns:
        A mapping the gate can scan, keyed by a real autonomy level.
    """
    return {
        AutonomyLevel.SUPERVISED: AutonomyPreset(
            level=AutonomyLevel.SUPERVISED,
            description="fixture",
            auto_approve=auto_approve,
            human_approval=(),
            security_agent=True,
        )
    }


class TestDetection:
    def test_a_category_grant_reporting_every_undeclared_member(self) -> None:
        """The shape that shipped: one prefix, one member nobody decided on."""
        violations = scan_presets(
            _preset("design"),
            confined=frozenset({ActionType.DESIGN_GENERATE}),
        )

        assert [v.action_type for v in violations] == [ActionType.DESIGN_DELETE]
        assert violations[0].pattern == "design"

    def test_a_concrete_grant_outside_the_declaration_is_a_violation(self) -> None:
        violations = scan_presets(_preset("vcs:push"), confined=frozenset())

        assert [v.action_type for v in violations] == [ActionType.VCS_PUSH]

    def test_a_fully_declared_grant_passes(self) -> None:
        violations = scan_presets(
            _preset("test"),
            confined=frozenset({ActionType.TEST_RUN, ActionType.TEST_WRITE}),
        )

        assert violations == []

    def test_the_all_grant_is_not_judged(self) -> None:
        """FULL means everything and its description says so."""
        assert scan_presets(_preset("all"), confined=frozenset()) == []


class TestExpansion:
    def test_a_category_expands_to_its_builtin_members(self) -> None:
        expanded = _expand(ActionTypeRegistry(), "test")

        assert expanded == frozenset({ActionType.TEST_RUN, ActionType.TEST_WRITE})

    def test_a_custom_type_never_joins_a_category_grant(self) -> None:
        """The hole the gate cannot see from a static tree, closed at source."""
        registry = ActionTypeRegistry(custom_types=frozenset({"code:exfiltrate"}))

        assert "code:exfiltrate" not in _expand(registry, "code")

    def test_an_unknown_pattern_is_carried_through_as_itself(self) -> None:
        """So a typo surfaces as an undeclared type rather than vanishing."""
        assert _expand(ActionTypeRegistry(), "cdoe") == frozenset({"cdoe"})


class TestShippedPresets:
    def test_the_built_in_presets_pass(self) -> None:
        assert scan_presets() == []

    def test_main_reports_success(self) -> None:
        assert main([]) == 0

    @pytest.mark.parametrize(
        "action_type",
        [
            ActionType.DESIGN_GENERATE,
            ActionType.DESIGN_DELETE,
            ActionType.VCS_PUSH,
            ActionType.COMMS_EXTERNAL,
            ActionType.DEPLOY_STAGING,
        ],
    )
    def test_what_leaves_the_box_is_not_declared_confined(
        self,
        action_type: ActionType,
    ) -> None:
        assert action_type not in WORKTREE_CONFINED_ACTION_TYPES

    def test_the_declaration_names_only_real_action_types(self) -> None:
        """A stale entry would silently permit a type that no longer exists."""
        assert {member.value for member in ActionType} >= WORKTREE_CONFINED_ACTION_TYPES

    def test_no_preset_auto_approves_reaching_a_remote(self) -> None:
        """The verb SUPERVISED gates by name, checked at every level."""
        for preset in BUILTIN_PRESETS.values():
            if "all" in preset.auto_approve:
                continue
            expanded = frozenset(
                action_type
                for pattern in preset.auto_approve
                for action_type in _expand(ActionTypeRegistry(), pattern)
            )
            assert ActionType.VCS_PUSH not in expanded
