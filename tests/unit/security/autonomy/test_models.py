"""Tests for autonomy models -- presets, config, effective autonomy, overrides."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.security.autonomy.enums import DowngradeReason
from synthorg.security.autonomy.models import (
    BUILTIN_PRESETS,
    AutonomyConfig,
    AutonomyOverride,
    AutonomyPreset,
    AutonomyUpdate,
    AutonomyUpdateResult,
    EffectiveAutonomy,
)


class TestAutonomyUpdate:
    """AutonomyUpdate validation tests."""

    @pytest.mark.unit
    def test_valid_update(self) -> None:
        update = AutonomyUpdate(
            requested_level=AutonomyLevel.SEMI,
            reason="agent has earned trust",
            requested_by="alice",
        )
        assert update.requested_level == AutonomyLevel.SEMI
        assert update.reason == "agent has earned trust"
        assert update.requested_by == "alice"

    @pytest.mark.unit
    def test_requested_by_optional(self) -> None:
        update = AutonomyUpdate(
            requested_level=AutonomyLevel.LOCKED,
            reason="incident response",
        )
        assert update.requested_by is None

    @pytest.mark.unit
    def test_blank_reason_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AutonomyUpdate(
                requested_level=AutonomyLevel.FULL,
                reason="",
            )

    @pytest.mark.unit
    def test_short_reason_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AutonomyUpdate(
                requested_level=AutonomyLevel.FULL,
                reason="ok",
            )

    @pytest.mark.unit
    def test_whitespace_padded_short_reason_rejected(self) -> None:
        # The validator strips before measuring length; a single
        # non-whitespace char between padding still fails the 3-char
        # floor. Pin this so the strip-before-length branch cannot
        # regress to a raw-length check.
        with pytest.raises(ValidationError):
            AutonomyUpdate(
                requested_level=AutonomyLevel.FULL,
                reason=" a ",
            )

    @pytest.mark.unit
    def test_frozen(self) -> None:
        update = AutonomyUpdate(
            requested_level=AutonomyLevel.SEMI,
            reason="thawing",
        )
        with pytest.raises(ValidationError):
            update.reason = "mutated"  # type: ignore[misc]


class TestAutonomyUpdateResult:
    """AutonomyUpdateResult validation tests."""

    @pytest.mark.unit
    def test_pending_default(self) -> None:
        result = AutonomyUpdateResult(
            agent_id="agent-1",
            current_level=AutonomyLevel.SUPERVISED,
            requested_level=AutonomyLevel.SEMI,
        )
        assert result.promotion_pending is True
        assert result.approval_id is None

    @pytest.mark.unit
    def test_with_approval_id(self) -> None:
        result = AutonomyUpdateResult(
            agent_id="agent-1",
            current_level=AutonomyLevel.SUPERVISED,
            requested_level=AutonomyLevel.SEMI,
            approval_id="approval-42",
        )
        assert result.approval_id == "approval-42"

    @pytest.mark.unit
    def test_blank_agent_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AutonomyUpdateResult(
                agent_id="",
                current_level=AutonomyLevel.SUPERVISED,
                requested_level=AutonomyLevel.SEMI,
            )


class TestAutonomyPreset:
    """AutonomyPreset validation tests."""

    @pytest.mark.unit
    def test_valid_preset(self) -> None:
        preset = AutonomyPreset(
            level=AutonomyLevel.SEMI,
            description="Test preset",
            auto_approve=("code:read",),
            human_approval=("deploy:production",),
        )
        assert preset.level == AutonomyLevel.SEMI
        assert preset.auto_approve == ("code:read",)
        assert preset.human_approval == ("deploy:production",)
        assert preset.security_agent is True

    @pytest.mark.unit
    def test_disjoint_enforcement(self) -> None:
        with pytest.raises(ValueError, match="disjoint"):
            AutonomyPreset(
                level=AutonomyLevel.SEMI,
                description="Overlapping",
                auto_approve=("code:read", "code:write"),
                human_approval=("code:write",),
            )

    @pytest.mark.unit
    def test_empty_lists_valid(self) -> None:
        preset = AutonomyPreset(
            level=AutonomyLevel.LOCKED,
            description="Empty",
            auto_approve=(),
            human_approval=(),
        )
        assert preset.auto_approve == ()
        assert preset.human_approval == ()


class TestBuiltinPresets:
    """Validate the four built-in presets."""

    @pytest.mark.unit
    def test_all_levels_present(self) -> None:
        for level in AutonomyLevel:
            assert level in BUILTIN_PRESETS, f"Missing preset for {level}"

    @pytest.mark.unit
    def test_full_preset_auto_approves_all(self) -> None:
        full = BUILTIN_PRESETS[AutonomyLevel.FULL]
        assert "all" in full.auto_approve
        assert full.human_approval == ()
        assert full.security_agent is False

    @pytest.mark.unit
    def test_locked_preset_requires_all_human(self) -> None:
        locked = BUILTIN_PRESETS[AutonomyLevel.LOCKED]
        assert locked.auto_approve == ()
        assert "all" in locked.human_approval
        assert locked.security_agent is True

    @pytest.mark.unit
    def test_semi_preset_has_both(self) -> None:
        semi = BUILTIN_PRESETS[AutonomyLevel.SEMI]
        assert len(semi.auto_approve) > 0
        assert len(semi.human_approval) > 0

    @pytest.mark.unit
    def test_supervised_preset_read_only_auto(self) -> None:
        supervised = BUILTIN_PRESETS[AutonomyLevel.SUPERVISED]
        assert "code:read" in supervised.auto_approve
        assert "code:write" in supervised.human_approval

    @pytest.mark.unit
    def test_presets_are_disjoint(self) -> None:
        for level, preset in BUILTIN_PRESETS.items():
            overlap = set(preset.auto_approve) & set(preset.human_approval)
            assert overlap == set(), (
                f"Preset {level} has overlapping entries: {overlap}"
            )


class TestAutonomyConfig:
    """AutonomyConfig validation tests."""

    @pytest.mark.unit
    def test_default_config(self) -> None:
        # Default flipped SEMI -> SUPERVISED (2026-04-23, #1538): fresh
        # installs now queue approvals for state-mutating actions.
        config = AutonomyConfig()
        assert config.level == AutonomyLevel.SUPERVISED
        assert len(config.presets) == len(AutonomyLevel)

    @pytest.mark.unit
    def test_custom_level(self) -> None:
        config = AutonomyConfig(level=AutonomyLevel.FULL)
        assert config.level == AutonomyLevel.FULL

    @pytest.mark.unit
    def test_level_must_be_in_presets(self) -> None:
        custom_presets: dict[str, AutonomyPreset] = {
            "semi": BUILTIN_PRESETS[AutonomyLevel.SEMI],
        }
        with pytest.raises(ValueError, match="not found in presets"):
            AutonomyConfig(level=AutonomyLevel.FULL, presets=custom_presets)

    @pytest.mark.unit
    def test_config_frozen(self) -> None:
        config = AutonomyConfig()
        with pytest.raises(ValidationError):
            config.level = AutonomyLevel.FULL  # type: ignore[misc]


class TestEffectiveAutonomy:
    """EffectiveAutonomy model tests."""

    @pytest.mark.unit
    def test_creation(self) -> None:
        effective = EffectiveAutonomy(
            level=AutonomyLevel.SEMI,
            auto_approve_actions=frozenset({"code:read"}),
            human_approval_actions=frozenset({"deploy:production"}),
            security_agent=True,
        )
        assert effective.level == AutonomyLevel.SEMI
        assert "code:read" in effective.auto_approve_actions
        assert "deploy:production" in effective.human_approval_actions

    @pytest.mark.unit
    def test_frozen(self) -> None:
        effective = EffectiveAutonomy(
            level=AutonomyLevel.FULL,
            auto_approve_actions=frozenset(),
            human_approval_actions=frozenset(),
            security_agent=False,
        )
        with pytest.raises(ValidationError):
            effective.level = AutonomyLevel.LOCKED  # type: ignore[misc]

    @pytest.mark.unit
    def test_disjoint_overlap_raises(self) -> None:
        with pytest.raises(ValidationError, match="disjoint"):
            EffectiveAutonomy(
                level=AutonomyLevel.SEMI,
                auto_approve_actions=frozenset({"code:read", "code:write"}),
                human_approval_actions=frozenset({"code:write", "deploy:prod"}),
                security_agent=True,
            )


class TestBuiltinPresetsImmutability:
    """BUILTIN_PRESETS should be a read-only mapping."""

    @pytest.mark.unit
    def test_cannot_assign_new_key(self) -> None:
        with pytest.raises(TypeError):
            BUILTIN_PRESETS["new"] = BUILTIN_PRESETS[AutonomyLevel.FULL]  # type: ignore[index]

    @pytest.mark.unit
    def test_cannot_delete_key(self) -> None:
        with pytest.raises(TypeError):
            del BUILTIN_PRESETS[AutonomyLevel.FULL]  # type: ignore[attr-defined]


class TestAutonomyOverride:
    """AutonomyOverride model tests."""

    @pytest.mark.unit
    def test_creation(self) -> None:
        now = datetime.now(UTC)
        override = AutonomyOverride(
            agent_id="agent-1",
            original_level=AutonomyLevel.SEMI,
            current_level=AutonomyLevel.SUPERVISED,
            reason=DowngradeReason.HIGH_ERROR_RATE,
            downgraded_at=now,
        )
        assert override.agent_id == "agent-1"
        assert override.requires_human_recovery is True

    @pytest.mark.unit
    def test_override_frozen(self) -> None:
        now = datetime.now(UTC)
        override = AutonomyOverride(
            agent_id="agent-1",
            original_level=AutonomyLevel.FULL,
            current_level=AutonomyLevel.LOCKED,
            reason=DowngradeReason.SECURITY_INCIDENT,
            downgraded_at=now,
        )
        with pytest.raises(ValidationError):
            override.current_level = AutonomyLevel.FULL  # type: ignore[misc]

    @pytest.mark.unit
    def test_current_above_original_rejected(self) -> None:
        """Downgrade validator rejects current_level > original_level."""
        now = datetime.now(UTC)
        with pytest.raises(ValidationError, match="higher than"):
            AutonomyOverride(
                agent_id="agent-1",
                original_level=AutonomyLevel.SUPERVISED,
                current_level=AutonomyLevel.FULL,
                reason=DowngradeReason.HIGH_ERROR_RATE,
                downgraded_at=now,
            )
