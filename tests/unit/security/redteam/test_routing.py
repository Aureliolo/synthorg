"""Unit tests for severity x autonomy verdict routing."""

import pytest

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.security.redteam.models import (
    RedTeamAttackSurface,
    RedTeamFinding,
    RedTeamSeverity,
    RedTeamVerdict,
)
from synthorg.security.redteam.routing import (
    AUTONOMY_LEVELS_THAT_BLOCK_MEDIUM,
    HEURISTIC_GROUNDING_MAX_SEVERITY,
    SEVERITY_ALWAYS_BLOCK_FROM,
    SUBSTRATE_DROP_FLOOR,
    SUBSTRATE_GROUNDING_MAX_SEVERITY,
    SUBSTRATE_HIGH_CONFIDENCE_FLOOR,
    SUBSTRATE_LOW_CONFIDENCE_FLOOR,
    SUBSTRATE_MEDIUM_CONFIDENCE_FLOOR,
    compute_red_team_verdict,
    should_block,
    substrate_severity_for_confidence,
)


def _finding(severity: RedTeamSeverity) -> RedTeamFinding:
    if severity in (RedTeamSeverity.HIGH, RedTeamSeverity.CRITICAL):
        return RedTeamFinding(
            attack_surface=RedTeamAttackSurface.CORRECTNESS,
            severity=severity,
            description="defect",
            evidence=("L1",),
        )
    return RedTeamFinding(
        attack_surface=RedTeamAttackSurface.CORRECTNESS,
        severity=severity,
        description="defect",
    )


_ALL_AUTONOMY: tuple[AutonomyLevel, ...] = (
    AutonomyLevel.LOCKED,
    AutonomyLevel.SUPERVISED,
    AutonomyLevel.SEMI,
    AutonomyLevel.FULL,
)


@pytest.mark.unit
class TestShouldBlock:
    """``should_block`` matrix coverage."""

    @pytest.mark.parametrize("autonomy", _ALL_AUTONOMY)
    def test_critical_blocks_at_every_autonomy(self, autonomy: AutonomyLevel) -> None:
        assert should_block(RedTeamSeverity.CRITICAL, autonomy) is True

    @pytest.mark.parametrize("autonomy", _ALL_AUTONOMY)
    def test_high_blocks_at_every_autonomy(self, autonomy: AutonomyLevel) -> None:
        assert should_block(RedTeamSeverity.HIGH, autonomy) is True

    @pytest.mark.parametrize(
        "autonomy",
        [AutonomyLevel.LOCKED, AutonomyLevel.SUPERVISED],
    )
    def test_medium_blocks_under_low_autonomy(self, autonomy: AutonomyLevel) -> None:
        assert autonomy in AUTONOMY_LEVELS_THAT_BLOCK_MEDIUM
        assert should_block(RedTeamSeverity.MEDIUM, autonomy) is True

    @pytest.mark.parametrize(
        "autonomy",
        [a for a in _ALL_AUTONOMY if a not in AUTONOMY_LEVELS_THAT_BLOCK_MEDIUM],
    )
    def test_medium_informational_under_high_autonomy(
        self, autonomy: AutonomyLevel
    ) -> None:
        assert should_block(RedTeamSeverity.MEDIUM, autonomy) is False

    @pytest.mark.parametrize("autonomy", _ALL_AUTONOMY)
    def test_low_never_blocks(self, autonomy: AutonomyLevel) -> None:
        assert should_block(RedTeamSeverity.LOW, autonomy) is False

    @pytest.mark.parametrize("autonomy", _ALL_AUTONOMY)
    def test_info_never_blocks(self, autonomy: AutonomyLevel) -> None:
        assert should_block(RedTeamSeverity.INFO, autonomy) is False


@pytest.mark.unit
class TestComputeRedTeamVerdict:
    """``compute_red_team_verdict`` aggregates over findings."""

    @pytest.mark.parametrize("autonomy", _ALL_AUTONOMY)
    def test_no_findings_is_pass(self, autonomy: AutonomyLevel) -> None:
        verdict = compute_red_team_verdict((), autonomy)
        assert verdict is RedTeamVerdict.PASS

    def test_only_low_findings_is_pass_with_findings(self) -> None:
        verdict = compute_red_team_verdict(
            (_finding(RedTeamSeverity.LOW), _finding(RedTeamSeverity.INFO)),
            AutonomyLevel.LOCKED,
        )
        assert verdict is RedTeamVerdict.PASS_WITH_FINDINGS

    @pytest.mark.parametrize("autonomy", _ALL_AUTONOMY)
    def test_high_finding_blocks_regardless_of_autonomy(
        self, autonomy: AutonomyLevel
    ) -> None:
        verdict = compute_red_team_verdict(
            (
                _finding(RedTeamSeverity.HIGH),
                _finding(RedTeamSeverity.LOW),
            ),
            autonomy,
        )
        assert verdict is RedTeamVerdict.BLOCK

    @pytest.mark.parametrize("autonomy", _ALL_AUTONOMY)
    def test_critical_finding_blocks_regardless_of_autonomy(
        self, autonomy: AutonomyLevel
    ) -> None:
        verdict = compute_red_team_verdict(
            (_finding(RedTeamSeverity.CRITICAL),),
            autonomy,
        )
        assert verdict is RedTeamVerdict.BLOCK

    def test_medium_blocks_under_supervised(self) -> None:
        verdict = compute_red_team_verdict(
            (_finding(RedTeamSeverity.MEDIUM),),
            AutonomyLevel.SUPERVISED,
        )
        assert verdict is RedTeamVerdict.BLOCK

    def test_medium_informational_under_full(self) -> None:
        verdict = compute_red_team_verdict(
            (_finding(RedTeamSeverity.MEDIUM),),
            AutonomyLevel.FULL,
        )
        assert verdict is RedTeamVerdict.PASS_WITH_FINDINGS


@pytest.mark.unit
class TestRoutingConstants:
    """Routing constants encode the locked plan-doc matrix."""

    def test_severity_block_threshold_is_high(self) -> None:
        assert SEVERITY_ALWAYS_BLOCK_FROM is RedTeamSeverity.HIGH

    def test_autonomy_block_set_includes_locked_and_supervised(self) -> None:
        assert AutonomyLevel.LOCKED in AUTONOMY_LEVELS_THAT_BLOCK_MEDIUM
        assert AutonomyLevel.SUPERVISED in AUTONOMY_LEVELS_THAT_BLOCK_MEDIUM
        assert AutonomyLevel.FULL not in AUTONOMY_LEVELS_THAT_BLOCK_MEDIUM
        assert AutonomyLevel.SEMI not in AUTONOMY_LEVELS_THAT_BLOCK_MEDIUM

    def test_heuristic_grounding_ceiling_is_low(self) -> None:
        assert HEURISTIC_GROUNDING_MAX_SEVERITY is RedTeamSeverity.LOW

    def test_substrate_grounding_ceiling_is_high(self) -> None:
        assert SUBSTRATE_GROUNDING_MAX_SEVERITY is RedTeamSeverity.HIGH

    def test_substrate_confidence_floors_are_ordered(self) -> None:
        assert (
            SUBSTRATE_LOW_CONFIDENCE_FLOOR
            < SUBSTRATE_MEDIUM_CONFIDENCE_FLOOR
            < SUBSTRATE_HIGH_CONFIDENCE_FLOOR
        )
        assert SUBSTRATE_DROP_FLOOR == SUBSTRATE_LOW_CONFIDENCE_FLOOR


@pytest.mark.unit
class TestSubstrateSeverityForConfidence:
    """Banded confidence -> severity mapping for substrate claims."""

    @pytest.mark.parametrize(
        ("confidence", "expected"),
        [
            (1.0, RedTeamSeverity.HIGH),
            (0.85, RedTeamSeverity.HIGH),
            (0.84, RedTeamSeverity.MEDIUM),
            (0.65, RedTeamSeverity.MEDIUM),
            (0.64, RedTeamSeverity.LOW),
            (0.45, RedTeamSeverity.LOW),
            (0.44, None),
            (0.0, None),
        ],
    )
    def test_band_boundaries(
        self,
        confidence: float,
        expected: RedTeamSeverity | None,
    ) -> None:
        assert substrate_severity_for_confidence(confidence) is expected

    def test_high_band_returns_the_cap(self) -> None:
        # The HIGH band returns the cap constant directly, so the cap is
        # the single source of truth and never exceeded (no CRITICAL).
        assert (
            substrate_severity_for_confidence(0.99) is SUBSTRATE_GROUNDING_MAX_SEVERITY
        )
