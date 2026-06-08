# mypy: disable-error-code="explicit-any"
"""Planted-claim acceptance + precision tests for the substrate checker.

Acceptance contract:

    A deliverable carrying an ungrounded factual claim is caught by the
    substrate-backed grounding checker and BLOCKed before completion,
    EVEN WHEN the red-team agent itself files a clean report. A
    deliverable whose claims are grounded in the corpus is NOT blocked.

The tests drive the production :class:`RedTeamGateService` (the
simulation harness) with a scripted ``AgentRunner`` that files a
finding-free report, so the verdict is driven solely by the
:class:`KnowledgeSubstrateGroundingChecker`. The checker's knowledge
service and provider are scripted fakes: the planted case scripts an
"unsupported" entailment verdict; the precision case scripts "supported".
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.types import NotBlankStr
from synthorg.knowledge.enums import SourceType
from synthorg.knowledge.models import Citation, CodeLocator, KnowledgeHit
from synthorg.knowledge.service import KnowledgeService
from synthorg.providers.enums import FinishReason
from synthorg.providers.models import CompletionResponse, TokenUsage, ToolCall
from synthorg.providers.protocol import CompletionProvider
from synthorg.security.redteam import (
    InMemoryRedTeamReportRepository,
    RedTeamAttackSurface,
    RedTeamGateService,
    RedTeamReport,
    RedTeamReviewInput,
    RedTeamSeverity,
    RedTeamVerdict,
)
from synthorg.security.redteam.grounding.resolver import GroundingSubstrateContext
from synthorg.security.redteam.grounding.substrate import (
    KnowledgeSubstrateGroundingChecker,
)
from synthorg.security.redteam.protocol import AgentRunner
from tests._shared import FakeClock
from tests._shared.mock_of import mock_of

pytestmark = pytest.mark.integration

_HASH = "b" * 64
_MODEL = "example-medium-001"
_EXEC = "exec-substrate-001"
_TASK = "task-substrate-001"
_DELIVERABLE = NotBlankStr(
    "Revenue grew 47% last quarter, the best result in company history."
)
_CRITERIA: tuple[str, ...] = ("Report the quarterly revenue trend.",)


class _CleanReportRunner:
    """Scripted runner that files a finding-free report.

    The agent surfaces no defects, so any BLOCK verdict is attributable
    to the substrate grounding checker alone.
    """

    def __init__(self, *, repo: InMemoryRedTeamReportRepository) -> None:
        self._repo = repo
        self.invocations = 0

    async def run(self, *, review_input: RedTeamReviewInput) -> None:
        self.invocations += 1
        await self._repo.put(
            execution_id=review_input.execution_id,
            report=RedTeamReport(
                execution_id=review_input.execution_id,
                task_id=review_input.task_id,
                summary="No agent-identified defects.",
            ),
        )


def _hit() -> KnowledgeHit:
    return KnowledgeHit(
        chunk_text="The quarterly report does not state a 47% growth figure.",
        relevance_score=0.88,
        citation=Citation(
            source_id="src-finance",
            chunk_id="chunk-1",
            source_type=SourceType.REPO,
            title="Finance report",
            uri="repo://finance.md",
            locator=CodeLocator(path="finance.md", line_start=1, line_end=4),
            content_hash=_HASH,
        ),
    )


def _extract_response(claim: str) -> CompletionResponse:
    arguments: dict[str, Any] = {"claims": [claim]}
    return CompletionResponse(
        tool_calls=(ToolCall(id="x", name="extract_claims", arguments=arguments),),
        finish_reason=FinishReason.TOOL_USE,
        usage=TokenUsage(input_tokens=1, output_tokens=1, cost=0.0),
        model=_MODEL,
    )


def _verdict_response(verdict: str, confidence: float) -> CompletionResponse:
    arguments: dict[str, Any] = {
        "verdict": verdict,
        "confidence": confidence,
        "reason": "rationale",
    }
    return CompletionResponse(
        tool_calls=(ToolCall(id="y", name="grounding_verdict", arguments=arguments),),
        finish_reason=FinishReason.TOOL_USE,
        usage=TokenUsage(input_tokens=1, output_tokens=1, cost=0.0),
        model=_MODEL,
    )


def _substrate_checker(
    *,
    responses: list[CompletionResponse],
) -> KnowledgeSubstrateGroundingChecker:
    provider = mock_of[CompletionProvider](
        complete=AsyncMock(spec=CompletionProvider.complete, side_effect=responses),
    )
    knowledge = mock_of[KnowledgeService](
        search=AsyncMock(spec=KnowledgeService.search, return_value=(_hit(),)),
    )
    context = GroundingSubstrateContext(
        knowledge_service=knowledge,
        provider=provider,
        model_id=NotBlankStr(_MODEL),
        cost_tracker=None,
    )
    return KnowledgeSubstrateGroundingChecker(resolver=lambda: context)


def _review_input(
    autonomy: AutonomyLevel = AutonomyLevel.SUPERVISED,
) -> RedTeamReviewInput:
    return RedTeamReviewInput(
        task_id=_TASK,
        execution_id=_EXEC,
        deliverable_content=_DELIVERABLE,
        acceptance_criteria=_CRITERIA,
        assigned_agent_id="agent-analyst-3",
        autonomy=autonomy,
        project_id="proj-substrate",
    )


async def test_planted_ungrounded_claim_blocks_via_substrate_checker() -> None:
    """An ungrounded claim BLOCKs even when the agent files a clean report."""
    repo = InMemoryRedTeamReportRepository()
    runner: AgentRunner = _CleanReportRunner(repo=repo)
    checker = _substrate_checker(
        responses=[
            _extract_response("Revenue grew 47% last quarter."),
            _verdict_response("unsupported", 0.95),
        ]
    )
    gate = RedTeamGateService(
        agent_runner=runner,
        report_repo=repo,
        grounding_checker=checker,
        clock=FakeClock(),
    )

    result = await gate.evaluate(_review_input())

    assert result.verdict is RedTeamVerdict.BLOCK
    assert runner.invocations == 1  # type: ignore[attr-defined]
    grounding = [
        f
        for f in result.report.findings
        if f.attack_surface is RedTeamAttackSurface.GROUNDING
    ]
    assert any(
        f.severity is RedTeamSeverity.HIGH and f.source == "knowledge_substrate"
        for f in grounding
    )
    assert len(result.grounding_claims) == 1
    assert result.grounding_claims[0].source == "knowledge_substrate"


async def test_grounded_claim_is_not_blocked() -> None:
    """A claim the corpus supports does not produce a false-positive BLOCK."""
    repo = InMemoryRedTeamReportRepository()
    runner: AgentRunner = _CleanReportRunner(repo=repo)
    checker = _substrate_checker(
        responses=[
            _extract_response("Revenue grew 47% last quarter."),
            _verdict_response("supported", 0.95),
        ]
    )
    gate = RedTeamGateService(
        agent_runner=runner,
        report_repo=repo,
        grounding_checker=checker,
        clock=FakeClock(),
    )

    result = await gate.evaluate(_review_input())

    assert result.verdict is RedTeamVerdict.PASS
    assert result.grounding_claims == ()


async def test_medium_band_claim_blocks_under_supervised_autonomy() -> None:
    """A MEDIUM-confidence ungrounded claim BLOCKs under SUPERVISED autonomy."""
    repo = InMemoryRedTeamReportRepository()
    runner: AgentRunner = _CleanReportRunner(repo=repo)
    checker = _substrate_checker(
        responses=[
            _extract_response("Revenue grew 47% last quarter."),
            _verdict_response("unsupported", 0.70),
        ]
    )
    gate = RedTeamGateService(
        agent_runner=runner,
        report_repo=repo,
        grounding_checker=checker,
        clock=FakeClock(),
    )

    result = await gate.evaluate(_review_input(AutonomyLevel.SUPERVISED))

    assert result.verdict is RedTeamVerdict.BLOCK
    grounding = [
        f
        for f in result.report.findings
        if f.attack_surface is RedTeamAttackSurface.GROUNDING
    ]
    assert any(f.severity is RedTeamSeverity.MEDIUM for f in grounding)


async def test_medium_band_claim_does_not_block_under_full_autonomy() -> None:
    """The same MEDIUM claim surfaces without blocking under FULL autonomy."""
    repo = InMemoryRedTeamReportRepository()
    runner: AgentRunner = _CleanReportRunner(repo=repo)
    checker = _substrate_checker(
        responses=[
            _extract_response("Revenue grew 47% last quarter."),
            _verdict_response("unsupported", 0.70),
        ]
    )
    gate = RedTeamGateService(
        agent_runner=runner,
        report_repo=repo,
        grounding_checker=checker,
        clock=FakeClock(),
    )

    result = await gate.evaluate(_review_input(AutonomyLevel.FULL))

    assert result.verdict is RedTeamVerdict.PASS_WITH_FINDINGS
    assert len(result.grounding_claims) == 1
