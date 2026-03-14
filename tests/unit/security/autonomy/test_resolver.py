"""Tests for AutonomyResolver — resolution chain, expansion, seniority."""

import pytest

from synthorg.core.enums import ActionType, AutonomyLevel, SeniorityLevel
from synthorg.security.action_types import ActionTypeRegistry
from synthorg.security.autonomy.models import (
    BUILTIN_PRESETS,
    AutonomyConfig,
    AutonomyPreset,
)
from synthorg.security.autonomy.resolver import AutonomyResolver


def _make_resolver(
    *,
    level: AutonomyLevel = AutonomyLevel.SEMI,
    custom_types: frozenset[str] = frozenset(),
) -> AutonomyResolver:
    """Create a resolver with the given default level."""
    registry = ActionTypeRegistry(custom_types=custom_types)
    config = AutonomyConfig(level=level)
    return AutonomyResolver(registry=registry, config=config)


class TestResolutionChain:
    """Three-level resolution chain: agent → department → company."""

    @pytest.mark.unit
    def test_company_default(self) -> None:
        resolver = _make_resolver(level=AutonomyLevel.SEMI)
        result = resolver.resolve()
        assert result.level == AutonomyLevel.SEMI

    @pytest.mark.unit
    def test_department_override(self) -> None:
        resolver = _make_resolver(level=AutonomyLevel.SEMI)
        result = resolver.resolve(department_level=AutonomyLevel.SUPERVISED)
        assert result.level == AutonomyLevel.SUPERVISED

    @pytest.mark.unit
    def test_agent_overrides_department(self) -> None:
        resolver = _make_resolver(level=AutonomyLevel.SEMI)
        result = resolver.resolve(
            agent_level=AutonomyLevel.FULL,
            department_level=AutonomyLevel.SUPERVISED,
        )
        assert result.level == AutonomyLevel.FULL

    @pytest.mark.unit
    def test_agent_overrides_company(self) -> None:
        resolver = _make_resolver(level=AutonomyLevel.LOCKED)
        result = resolver.resolve(agent_level=AutonomyLevel.FULL)
        assert result.level == AutonomyLevel.FULL


class TestCategoryExpansion:
    """Category shortcut and 'all' expansion."""

    @pytest.mark.unit
    def test_category_expansion(self) -> None:
        resolver = _make_resolver(level=AutonomyLevel.SEMI)
        result = resolver.resolve()
        # SEMI auto-approves "code" category — includes code:read, etc.
        assert ActionType.CODE_READ in result.auto_approve_actions
        assert ActionType.CODE_WRITE in result.auto_approve_actions
        assert ActionType.CODE_CREATE in result.auto_approve_actions

    @pytest.mark.unit
    def test_all_shortcut_full(self) -> None:
        resolver = _make_resolver(level=AutonomyLevel.FULL)
        result = resolver.resolve()
        # FULL auto-approves "all" — should include every registered type.
        all_types = ActionTypeRegistry().all_types()
        assert result.auto_approve_actions == all_types

    @pytest.mark.unit
    def test_all_shortcut_locked(self) -> None:
        resolver = _make_resolver(level=AutonomyLevel.LOCKED)
        result = resolver.resolve()
        # LOCKED human_approval = "all"
        all_types = ActionTypeRegistry().all_types()
        assert result.human_approval_actions == all_types
        assert result.auto_approve_actions == frozenset()

    @pytest.mark.unit
    def test_concrete_action_types(self) -> None:
        resolver = _make_resolver(level=AutonomyLevel.SUPERVISED)
        result = resolver.resolve()
        # SUPERVISED auto-approves code:read, vcs:read, test:run, db:query
        assert ActionType.CODE_READ in result.auto_approve_actions
        assert ActionType.VCS_READ in result.auto_approve_actions
        assert ActionType.TEST_RUN in result.auto_approve_actions

    @pytest.mark.unit
    def test_custom_action_types_included(self) -> None:
        resolver = _make_resolver(
            level=AutonomyLevel.SEMI,
            custom_types=frozenset({"code:lint"}),
        )
        result = resolver.resolve()
        # "code" category expansion should include custom code:lint.
        assert "code:lint" in result.auto_approve_actions


class TestSeniorityValidation:
    """Seniority constraint: JUNIOR + FULL is rejected."""

    @pytest.mark.unit
    def test_junior_full_rejected(self) -> None:
        resolver = _make_resolver()
        with pytest.raises(ValueError, match="FULL autonomy"):
            resolver.validate_seniority(SeniorityLevel.JUNIOR, AutonomyLevel.FULL)

    @pytest.mark.unit
    def test_junior_semi_allowed(self) -> None:
        resolver = _make_resolver()
        resolver.validate_seniority(SeniorityLevel.JUNIOR, AutonomyLevel.SEMI)

    @pytest.mark.unit
    def test_mid_full_allowed(self) -> None:
        resolver = _make_resolver()
        resolver.validate_seniority(SeniorityLevel.MID, AutonomyLevel.FULL)

    @pytest.mark.unit
    @pytest.mark.parametrize("level", list(SeniorityLevel))
    def test_locked_always_allowed(self, level: SeniorityLevel) -> None:
        resolver = _make_resolver()
        resolver.validate_seniority(level, AutonomyLevel.LOCKED)


class TestMissingPreset:
    """Error when the resolved level has no preset."""

    @pytest.mark.unit
    def test_missing_preset_raises(self) -> None:
        custom_presets: dict[str, AutonomyPreset] = {
            "semi": BUILTIN_PRESETS[AutonomyLevel.SEMI],
        }
        config = AutonomyConfig(level=AutonomyLevel.SEMI, presets=custom_presets)
        resolver = AutonomyResolver(
            registry=ActionTypeRegistry(),
            config=config,
        )
        with pytest.raises(ValueError, match="No preset found"):
            resolver.resolve(agent_level=AutonomyLevel.FULL)
