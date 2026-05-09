"""Unit tests for meta-loop improvement strategies."""

import pytest
from pydantic import JsonValue

from synthorg.meta.config import SelfImprovementConfig
from synthorg.meta.models import (
    EvolutionMode,
    OrgBudgetSummary,
    OrgCoordinationSummary,
    OrgErrorSummary,
    OrgEvolutionSummary,
    OrgPerformanceSummary,
    OrgScalingSummary,
    OrgSignalSnapshot,
    OrgTelemetrySummary,
    ProposalAltitude,
    RuleMatch,
    RuleSeverity,
)
from synthorg.meta.strategies.architecture import (
    ArchitectureProposalStrategy,
)
from synthorg.meta.strategies.config_tuning import ConfigTuningStrategy
from synthorg.meta.strategies.prompt_tuning import PromptTuningStrategy

pytestmark = pytest.mark.unit

_DEFAULT_CONFIG = SelfImprovementConfig(enabled=True)


def _snap() -> OrgSignalSnapshot:
    return OrgSignalSnapshot(
        performance=OrgPerformanceSummary(
            avg_quality_score=4.0,
            avg_success_rate=0.6,
            avg_collaboration_score=5.0,
            agent_count=10,
        ),
        budget=OrgBudgetSummary(
            total_spend=150.0,
            productive_ratio=0.5,
            coordination_ratio=0.45,
            system_ratio=0.05,
            forecast_confidence=0.8,
            orchestration_overhead=0.9,
            days_until_exhausted=7,
        ),
        coordination=OrgCoordinationSummary(
            coordination_overhead_pct=40.0,
            straggler_gap_ratio=2.5,
        ),
        scaling=OrgScalingSummary(
            total_decisions=5,
            success_rate=0.4,
        ),
        errors=OrgErrorSummary(total_findings=15),
        evolution=OrgEvolutionSummary(),
        telemetry=OrgTelemetrySummary(),
    )


def _rule(
    name: str,
    altitudes: tuple[ProposalAltitude, ...],
    ctx: dict[str, JsonValue] | None = None,
) -> RuleMatch:
    return RuleMatch(
        rule_name=name,
        severity=RuleSeverity.WARNING,
        description=f"Test rule {name}",
        signal_context=ctx or {},
        suggested_altitudes=altitudes,
    )


# ── ConfigTuningStrategy ──────────────────────────────────────────


class TestConfigTuningStrategy:
    """Config tuning strategy tests."""

    def test_altitude(self) -> None:
        s = ConfigTuningStrategy(config=_DEFAULT_CONFIG)
        assert s.altitude == ProposalAltitude.CONFIG_TUNING

    async def test_quality_declining_generates_proposal(self) -> None:
        s = ConfigTuningStrategy(config=_DEFAULT_CONFIG)
        rules = (
            _rule(
                "quality_declining",
                (ProposalAltitude.CONFIG_TUNING,),
                {"avg_quality": 4.0, "agent_count": 10},
            ),
        )
        proposals = await s.propose(snapshot=_snap(), triggered_rules=rules)
        assert len(proposals) == 1
        assert proposals[0].altitude == ProposalAltitude.CONFIG_TUNING
        assert proposals[0].source_rule == "quality_declining"
        assert len(proposals[0].config_changes) >= 1

    async def test_budget_overrun_generates_proposal(self) -> None:
        s = ConfigTuningStrategy(config=_DEFAULT_CONFIG)
        rules = (
            _rule(
                "budget_overrun",
                (ProposalAltitude.CONFIG_TUNING,),
                {"days_until_exhausted": 7, "total_spend": 150.0},
            ),
        )
        proposals = await s.propose(snapshot=_snap(), triggered_rules=rules)
        assert len(proposals) == 1
        assert proposals[0].source_rule == "budget_overrun"

    async def test_ignores_non_config_rules(self) -> None:
        s = ConfigTuningStrategy(config=_DEFAULT_CONFIG)
        rules = (
            _rule(
                "quality_declining",
                (ProposalAltitude.ARCHITECTURE,),
            ),
        )
        proposals = await s.propose(snapshot=_snap(), triggered_rules=rules)
        assert len(proposals) == 0

    async def test_multiple_rules_multiple_proposals(self) -> None:
        s = ConfigTuningStrategy(config=_DEFAULT_CONFIG)
        rules = (
            _rule(
                "quality_declining",
                (ProposalAltitude.CONFIG_TUNING,),
                {"avg_quality": 4.0, "agent_count": 10},
            ),
            _rule(
                "budget_overrun",
                (ProposalAltitude.CONFIG_TUNING,),
                {"days_until_exhausted": 7, "total_spend": 150.0},
            ),
        )
        proposals = await s.propose(snapshot=_snap(), triggered_rules=rules)
        assert len(proposals) == 2

    async def test_unknown_rule_returns_none(self) -> None:
        s = ConfigTuningStrategy(config=_DEFAULT_CONFIG)
        rules = (
            _rule(
                "unknown_rule",
                (ProposalAltitude.CONFIG_TUNING,),
            ),
        )
        proposals = await s.propose(snapshot=_snap(), triggered_rules=rules)
        assert len(proposals) == 0

    async def test_all_known_rules_produce_proposals(self) -> None:
        s = ConfigTuningStrategy(config=_DEFAULT_CONFIG)
        known: list[tuple[str, dict[str, JsonValue]]] = [
            ("quality_declining", {"avg_quality": 4.0, "agent_count": 10}),
            ("success_rate_drop", {"avg_success_rate": 0.6}),
            ("budget_overrun", {"days_until_exhausted": 7, "total_spend": 150}),
            ("coordination_cost_ratio", {"coordination_ratio": 0.45}),
            ("coordination_overhead", {"overhead_pct": 40.0}),
            ("scaling_failure", {"failure_rate": 0.6, "total_decisions": 5}),
        ]
        for name, ctx in known:
            rules = (_rule(name, (ProposalAltitude.CONFIG_TUNING,), ctx),)
            proposals = await s.propose(snapshot=_snap(), triggered_rules=rules)
            assert len(proposals) == 1, f"No proposal for {name}"


# ── ArchitectureProposalStrategy ──────────────────────────────────


class TestArchitectureProposalStrategy:
    """Architecture proposal strategy tests."""

    def test_altitude(self) -> None:
        s = ArchitectureProposalStrategy(config=_DEFAULT_CONFIG)
        assert s.altitude == ProposalAltitude.ARCHITECTURE

    async def test_coordination_cost_generates_restructure(
        self,
    ) -> None:
        s = ArchitectureProposalStrategy(config=_DEFAULT_CONFIG)
        rules = (
            _rule(
                "coordination_cost_ratio",
                (ProposalAltitude.ARCHITECTURE,),
                {"coordination_ratio": 0.45},
            ),
        )
        proposals = await s.propose(snapshot=_snap(), triggered_rules=rules)
        assert len(proposals) == 1
        assert proposals[0].altitude == ProposalAltitude.ARCHITECTURE
        assert len(proposals[0].architecture_changes) >= 1

    async def test_straggler_generates_specialist(self) -> None:
        s = ArchitectureProposalStrategy(config=_DEFAULT_CONFIG)
        rules = (
            _rule(
                "straggler_bottleneck",
                (ProposalAltitude.ARCHITECTURE,),
                {"straggler_gap_ratio": 2.5},
            ),
        )
        proposals = await s.propose(snapshot=_snap(), triggered_rules=rules)
        assert len(proposals) == 1

    async def test_ignores_non_architecture_rules(self) -> None:
        s = ArchitectureProposalStrategy(config=_DEFAULT_CONFIG)
        rules = (
            _rule(
                "quality_declining",
                (ProposalAltitude.CONFIG_TUNING,),
            ),
        )
        proposals = await s.propose(snapshot=_snap(), triggered_rules=rules)
        assert len(proposals) == 0


# ── PromptTuningStrategy ──────────────────────────────────────────


class TestPromptTuningStrategy:
    """Prompt tuning strategy tests."""

    def test_altitude(self) -> None:
        s = PromptTuningStrategy(config=_DEFAULT_CONFIG)
        assert s.altitude == ProposalAltitude.PROMPT_TUNING

    async def test_quality_generates_principle(self) -> None:
        s = PromptTuningStrategy(config=_DEFAULT_CONFIG)
        rules = (
            _rule(
                "quality_declining",
                (ProposalAltitude.PROMPT_TUNING,),
            ),
        )
        proposals = await s.propose(snapshot=_snap(), triggered_rules=rules)
        assert len(proposals) == 1
        assert proposals[0].altitude == ProposalAltitude.PROMPT_TUNING
        assert len(proposals[0].prompt_changes) >= 1
        assert proposals[0].prompt_changes[0].evolution_mode == EvolutionMode.ORG_WIDE

    async def test_error_spike_generates_awareness(self) -> None:
        s = PromptTuningStrategy(config=_DEFAULT_CONFIG)
        rules = (
            _rule(
                "error_spike",
                (ProposalAltitude.PROMPT_TUNING,),
                {"total_findings": 15},
            ),
        )
        proposals = await s.propose(snapshot=_snap(), triggered_rules=rules)
        assert len(proposals) == 1
        assert proposals[0].source_rule == "error_spike"

    async def test_respects_configured_mode(self) -> None:
        from synthorg.meta.config import PromptTuningConfig

        cfg = SelfImprovementConfig(
            enabled=True,
            prompt_tuning=PromptTuningConfig(
                default_evolution_mode=EvolutionMode.ADVISORY,
            ),
        )
        s = PromptTuningStrategy(config=cfg)
        rules = (
            _rule(
                "quality_declining",
                (ProposalAltitude.PROMPT_TUNING,),
            ),
        )
        proposals = await s.propose(snapshot=_snap(), triggered_rules=rules)
        assert len(proposals) == 1
        assert proposals[0].prompt_changes[0].evolution_mode == EvolutionMode.ADVISORY

    async def test_ignores_non_prompt_rules(self) -> None:
        s = PromptTuningStrategy(config=_DEFAULT_CONFIG)
        rules = (
            _rule(
                "budget_overrun",
                (ProposalAltitude.CONFIG_TUNING,),
            ),
        )
        proposals = await s.propose(snapshot=_snap(), triggered_rules=rules)
        assert len(proposals) == 0
