"""Integration tests for the code modification meta-loop cycle.

Tests the full pipeline: signals -> rules -> code modification
strategy -> guards -> proposal ready for approval.
"""

import json
from unittest.mock import AsyncMock

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.meta.config import CodeModificationConfig, SelfImprovementConfig
from synthorg.meta.models import (
    OrgBudgetSummary,
    OrgCoordinationSummary,
    OrgErrorSummary,
    OrgEvolutionSummary,
    OrgPerformanceSummary,
    OrgScalingSummary,
    OrgSignalSnapshot,
    OrgTelemetrySummary,
    ProposalAltitude,
    ProposalStatus,
)
from synthorg.meta.service import SelfImprovementService
from synthorg.settings.resolver import ConfigResolver
from tests._shared import mock_of
from tests._shared.model_binding import bound_ref, one_connection

pytestmark = pytest.mark.integration

_CODE_MOD_CFG = CodeModificationConfig(
    github_token="test-token",
    github_repo="test/repo",
)


def _snap(
    *,
    quality: float = 7.5,
    error_findings: int = 0,
) -> OrgSignalSnapshot:
    return OrgSignalSnapshot(
        performance=OrgPerformanceSummary(
            avg_quality_score=quality,
            avg_success_rate=0.85,
            avg_collaboration_score=6.0,
            agent_count=10,
        ),
        budget=OrgBudgetSummary(
            total_spend=150.0,
            productive_ratio=0.6,
            coordination_ratio=0.3,
            system_ratio=0.1,
            forecast_confidence=0.8,
            orchestration_overhead=0.5,
        ),
        coordination=OrgCoordinationSummary(),
        scaling=OrgScalingSummary(),
        errors=OrgErrorSummary(total_findings=error_findings),
        evolution=OrgEvolutionSummary(),
        telemetry=OrgTelemetrySummary(),
    )


def _valid_llm_response() -> str:
    return json.dumps(
        [
            {
                "file_path": "src/synthorg/meta/strategies/improved.py",
                "operation": "create",
                "old_content": "",
                "new_content": "class ImprovedStrategy:\n    pass\n",
                "description": "Add improved strategy",
                "reasoning": "Quality declining needs better approach",
            },
        ]
    )


def _mock_provider() -> AsyncMock:
    provider = AsyncMock()
    mock_response = AsyncMock()
    mock_response.content = _valid_llm_response()
    provider.complete = AsyncMock(return_value=mock_response)
    return provider


def _resolver() -> ConfigResolver:
    """Resolve the operator's code-modification pair for every read.

    Returns:
        A resolver naming the connection ``_mock_provider`` is registered as.
    """
    resolver: ConfigResolver = mock_of[ConfigResolver](
        get_str=AsyncMock(return_value=bound_ref("example-medium-001")),
    )
    return resolver


class TestCodeModificationCycleIntegration:
    """End-to-end cycle with code modification altitude."""

    async def test_quality_decline_triggers_code_modification(
        self,
    ) -> None:
        """Quality decline triggers code modification proposal.

        Signal pattern -> quality_declining rule fires ->
        code modification strategy generates proposal via LLM ->
        guard chain passes (code_modification enabled) ->
        proposal ready for approval.
        """
        provider = _mock_provider()
        svc = SelfImprovementService(
            config=SelfImprovementConfig(
                enabled=True,
                config_tuning_enabled=True,
                code_modification_enabled=True,
                code_modification=_CODE_MOD_CFG,
            ),
            connections=one_connection(provider),
            config_resolver=_resolver(),
            approval_store=ApprovalStore(),
        )
        proposals = await svc.run_cycle(_snap(quality=4.0))

        code_proposals = [
            p for p in proposals if p.altitude == ProposalAltitude.CODE_MODIFICATION
        ]
        assert len(code_proposals) >= 1
        cp = code_proposals[0]
        assert cp.status == ProposalStatus.PENDING
        assert cp.source_rule == "quality_declining"
        assert len(cp.code_changes) >= 1
        assert cp.rollback_plan.operations

    async def test_code_modification_disabled_blocks_proposals(
        self,
    ) -> None:
        """Code modification disabled -> proposals rejected by scope guard."""
        provider = _mock_provider()
        svc = SelfImprovementService(
            config=SelfImprovementConfig(
                enabled=True,
                config_tuning_enabled=True,
                code_modification_enabled=False,
            ),
            connections=one_connection(provider),
            config_resolver=_resolver(),
            approval_store=ApprovalStore(),
        )
        proposals = await svc.run_cycle(_snap(quality=4.0))
        for p in proposals:
            assert p.altitude != ProposalAltitude.CODE_MODIFICATION

    async def test_error_spike_triggers_code_modification(
        self,
    ) -> None:
        """Error spike also triggers code modification proposals."""
        provider = _mock_provider()
        svc = SelfImprovementService(
            config=SelfImprovementConfig(
                enabled=True,
                config_tuning_enabled=False,
                prompt_tuning_enabled=False,
                code_modification_enabled=True,
                code_modification=_CODE_MOD_CFG,
            ),
            connections=one_connection(provider),
            config_resolver=_resolver(),
            approval_store=ApprovalStore(),
        )
        proposals = await svc.run_cycle(
            _snap(error_findings=20),
        )
        code_proposals = [
            p for p in proposals if p.altitude == ProposalAltitude.CODE_MODIFICATION
        ]
        assert len(code_proposals) >= 1
        assert code_proposals[0].source_rule == "error_spike"

    async def test_healthy_org_no_code_modification(self) -> None:
        """Healthy signals -> no rules fire -> no code modification."""
        provider = _mock_provider()
        svc = SelfImprovementService(
            config=SelfImprovementConfig(
                enabled=True,
                code_modification_enabled=True,
                code_modification=_CODE_MOD_CFG,
            ),
            connections=one_connection(provider),
            config_resolver=_resolver(),
            approval_store=ApprovalStore(),
        )
        proposals = await svc.run_cycle(_snap())
        code_proposals = [
            p for p in proposals if p.altitude == ProposalAltitude.CODE_MODIFICATION
        ]
        assert code_proposals == []
