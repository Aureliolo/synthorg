# mypy: disable-error-code="explicit-any"
"""Unit tests for the substrate-backed grounding checker.

The checker's contract is precision: a claim grounded in the corpus is
not flagged, an unsupported claim with high confidence is, an empty
corpus never blocks, and any substrate gap degrades to the deterministic
heuristic rather than emitting blocking findings on no evidence.
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.core.enums import SourceType
from synthorg.core.types import NotBlankStr
from synthorg.knowledge.models import Citation, CodeLocator, KnowledgeHit
from synthorg.knowledge.service import KnowledgeService
from synthorg.providers.enums import FinishReason
from synthorg.providers.models import (
    CompletionResponse,
    TokenUsage,
    ToolCall,
)
from synthorg.providers.protocol import CompletionProvider
from synthorg.security.redteam.grounding.resolver import GroundingSubstrateContext
from synthorg.security.redteam.grounding.substrate import (
    KnowledgeSubstrateGroundingChecker,
)
from tests._shared.mock_of import mock_of

pytestmark = pytest.mark.unit

_HASH = "a" * 64
_MODEL = "example-medium-001"
_EXEC = NotBlankStr("exec-1")
_PROJECT = NotBlankStr("proj-1")
_NUMERIC_DELIVERABLE = NotBlankStr(
    "Revenue grew 47% last quarter compared to the prior period."
)


def _hit(text: str = "Quarterly revenue rose by 47 percent.") -> KnowledgeHit:
    return KnowledgeHit(
        chunk_text=text,
        relevance_score=0.92,
        citation=Citation(
            source_id="src-1",
            chunk_id="chunk-1",
            source_type=SourceType.REPO,
            title="Finance report",
            uri="repo://finance.md",
            locator=CodeLocator(path="finance.md", line_start=1, line_end=3),
            content_hash=_HASH,
        ),
    )


def _extract_response(claims: list[str]) -> CompletionResponse:
    arguments: dict[str, Any] = {"claims": claims}
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


def _no_tool_response() -> CompletionResponse:
    return CompletionResponse(
        content="I could not return a structured verdict.",
        tool_calls=(),
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=1, output_tokens=1, cost=0.0),
        model=_MODEL,
    )


def _provider(responses: list[CompletionResponse]) -> Any:
    return mock_of[CompletionProvider](
        complete=AsyncMock(spec=CompletionProvider.complete, side_effect=responses),
    )


def _knowledge(hits: tuple[KnowledgeHit, ...]) -> Any:
    return mock_of[KnowledgeService](
        search=AsyncMock(spec=KnowledgeService.search, return_value=hits),
    )


def _context(
    *,
    provider: Any,
    knowledge_service: Any,
) -> GroundingSubstrateContext:
    return GroundingSubstrateContext(
        knowledge_service=knowledge_service,
        provider=provider,
        model_id=NotBlankStr(_MODEL),
        cost_tracker=None,
    )


def _checker(
    context: GroundingSubstrateContext | None,
) -> KnowledgeSubstrateGroundingChecker:
    return KnowledgeSubstrateGroundingChecker(resolver=lambda: context)


class TestPrecision:
    async def test_supported_claim_is_not_flagged(self) -> None:
        provider = _provider(
            [
                _extract_response(["Revenue grew 47% last quarter."]),
                _verdict_response("supported", 0.95),
            ]
        )
        checker = _checker(
            _context(provider=provider, knowledge_service=_knowledge((_hit(),)))
        )

        claims = await checker.check(
            deliverable_content=_NUMERIC_DELIVERABLE,
            execution_id=_EXEC,
            project_id=_PROJECT,
        )

        assert claims == ()

    async def test_uncertain_verdict_is_not_flagged(self) -> None:
        provider = _provider(
            [
                _extract_response(["Revenue grew 47% last quarter."]),
                _verdict_response("uncertain", 0.99),
            ]
        )
        checker = _checker(
            _context(provider=provider, knowledge_service=_knowledge((_hit(),)))
        )

        claims = await checker.check(
            deliverable_content=_NUMERIC_DELIVERABLE,
            execution_id=_EXEC,
            project_id=_PROJECT,
        )

        assert claims == ()

    async def test_confidence_below_drop_floor_is_not_flagged(self) -> None:
        provider = _provider(
            [
                _extract_response(["Revenue grew 47% last quarter."]),
                _verdict_response("unsupported", 0.3),
            ]
        )
        checker = _checker(
            _context(provider=provider, knowledge_service=_knowledge((_hit(),)))
        )

        claims = await checker.check(
            deliverable_content=_NUMERIC_DELIVERABLE,
            execution_id=_EXEC,
        )

        assert claims == ()

    async def test_empty_corpus_does_not_flag_and_skips_entailment(self) -> None:
        knowledge = _knowledge(())
        provider = _provider([_extract_response(["Revenue grew 47% last quarter."])])
        checker = _checker(_context(provider=provider, knowledge_service=knowledge))

        claims = await checker.check(
            deliverable_content=_NUMERIC_DELIVERABLE,
            execution_id=_EXEC,
            project_id=_PROJECT,
        )

        assert claims == ()
        # Only the extraction call ran; no entailment on an empty corpus.
        assert provider.complete.await_count == 1  # type: ignore[attr-defined]
        knowledge.search.assert_awaited_once()  # type: ignore[attr-defined]
        _, kwargs = knowledge.search.call_args  # type: ignore[attr-defined]
        assert kwargs["project_id"] == _PROJECT


class TestEscalation:
    async def test_unsupported_high_confidence_claim_is_flagged(self) -> None:
        provider = _provider(
            [
                _extract_response(["Revenue grew 47% last quarter."]),
                _verdict_response("unsupported", 0.95),
            ]
        )
        checker = _checker(
            _context(provider=provider, knowledge_service=_knowledge((_hit(),)))
        )

        claims = await checker.check(
            deliverable_content=_NUMERIC_DELIVERABLE,
            execution_id=_EXEC,
            project_id=_PROJECT,
        )

        assert len(claims) == 1
        claim = claims[0]
        assert claim.source == "knowledge_substrate"
        assert claim.confidence == pytest.approx(0.95)
        assert "47%" in claim.excerpt

    async def test_search_scoped_to_project(self) -> None:
        knowledge = _knowledge((_hit(),))
        provider = _provider(
            [
                _extract_response(["Revenue grew 47% last quarter."]),
                _verdict_response("unsupported", 0.9),
            ]
        )
        checker = _checker(_context(provider=provider, knowledge_service=knowledge))

        await checker.check(
            deliverable_content=_NUMERIC_DELIVERABLE,
            execution_id=_EXEC,
            project_id=_PROJECT,
        )

        _, kwargs = knowledge.search.call_args  # type: ignore[attr-defined]
        assert kwargs["project_id"] == _PROJECT

    async def test_per_claim_search_failure_skips_only_that_claim(self) -> None:
        knowledge = mock_of[KnowledgeService](
            search=AsyncMock(
                spec=KnowledgeService.search,
                side_effect=[ValueError("boom"), (_hit(),)],
            ),
        )
        provider = _provider(
            [
                _extract_response(["First claim 10%.", "Second claim 47%."]),
                _verdict_response("unsupported", 0.9),
            ]
        )
        checker = _checker(_context(provider=provider, knowledge_service=knowledge))

        claims = await checker.check(
            deliverable_content=_NUMERIC_DELIVERABLE,
            execution_id=_EXEC,
            project_id=_PROJECT,
        )

        assert len(claims) == 1
        assert "47%" in claims[0].excerpt


class TestDegradation:
    async def test_resolver_returns_none_degrades_to_heuristic(self) -> None:
        checker = _checker(None)

        claims = await checker.check(
            deliverable_content=_NUMERIC_DELIVERABLE,
            execution_id=_EXEC,
        )

        assert len(claims) >= 1
        assert all(c.source == "heuristic" for c in claims)

    async def test_absent_knowledge_service_degrades_to_heuristic(self) -> None:
        provider = _provider([])
        checker = _checker(_context(provider=provider, knowledge_service=None))

        claims = await checker.check(
            deliverable_content=_NUMERIC_DELIVERABLE,
            execution_id=_EXEC,
        )

        assert len(claims) >= 1
        assert all(c.source == "heuristic" for c in claims)
        # The provider is never touched when the substrate is unavailable.
        provider.complete.assert_not_awaited()  # type: ignore[attr-defined]

    async def test_extraction_failure_degrades_to_heuristic(self) -> None:
        provider = mock_of[CompletionProvider](
            complete=AsyncMock(
                spec=CompletionProvider.complete,
                side_effect=ValueError("extraction exploded"),
            ),
        )
        checker = _checker(
            _context(provider=provider, knowledge_service=_knowledge((_hit(),)))
        )

        claims = await checker.check(
            deliverable_content=_NUMERIC_DELIVERABLE,
            execution_id=_EXEC,
        )

        assert len(claims) >= 1
        assert all(c.source == "heuristic" for c in claims)

    async def test_no_extracted_claims_returns_empty(self) -> None:
        knowledge = _knowledge((_hit(),))
        provider = _provider([_extract_response([])])
        checker = _checker(_context(provider=provider, knowledge_service=knowledge))

        claims = await checker.check(
            deliverable_content=_NUMERIC_DELIVERABLE,
            execution_id=_EXEC,
            project_id=_PROJECT,
        )

        assert claims == ()
        knowledge.search.assert_not_awaited()  # type: ignore[attr-defined]


class TestPerClaimResilience:
    async def test_entailment_failure_skips_only_that_claim(self) -> None:
        provider = mock_of[CompletionProvider](
            complete=AsyncMock(
                spec=CompletionProvider.complete,
                side_effect=[
                    _extract_response(["First claim 10%.", "Second claim 47%."]),
                    ValueError("entailment exploded"),
                    _verdict_response("unsupported", 0.9),
                ],
            ),
        )
        checker = _checker(
            _context(provider=provider, knowledge_service=_knowledge((_hit(),)))
        )

        claims = await checker.check(
            deliverable_content=_NUMERIC_DELIVERABLE,
            execution_id=_EXEC,
            project_id=_PROJECT,
        )

        assert len(claims) == 1
        assert "47%" in claims[0].excerpt

    async def test_unparseable_verdict_skips_claim(self) -> None:
        provider = _provider(
            [_extract_response(["Revenue grew 47%."]), _no_tool_response()]
        )
        checker = _checker(
            _context(provider=provider, knowledge_service=_knowledge((_hit(),)))
        )

        claims = await checker.check(
            deliverable_content=_NUMERIC_DELIVERABLE,
            execution_id=_EXEC,
            project_id=_PROJECT,
        )

        assert claims == ()

    async def test_multiple_unsupported_claims_all_flagged(self) -> None:
        provider = _provider(
            [
                _extract_response(["First claim 10%.", "Second claim 47%."]),
                _verdict_response("unsupported", 0.9),
                _verdict_response("unsupported", 0.88),
            ]
        )
        checker = _checker(
            _context(provider=provider, knowledge_service=_knowledge((_hit(),)))
        )

        claims = await checker.check(
            deliverable_content=_NUMERIC_DELIVERABLE,
            execution_id=_EXEC,
            project_id=_PROJECT,
        )

        assert len(claims) == 2


class TestDropFloorBoundary:
    async def test_confidence_exactly_at_drop_floor_is_flagged(self) -> None:
        provider = _provider(
            [
                _extract_response(["Revenue grew 47%."]),
                _verdict_response("unsupported", 0.45),
            ]
        )
        checker = _checker(
            _context(provider=provider, knowledge_service=_knowledge((_hit(),)))
        )

        claims = await checker.check(
            deliverable_content=_NUMERIC_DELIVERABLE,
            execution_id=_EXEC,
            project_id=_PROJECT,
        )

        assert len(claims) == 1

    async def test_confidence_just_below_drop_floor_is_not_flagged(self) -> None:
        provider = _provider(
            [
                _extract_response(["Revenue grew 47%."]),
                _verdict_response("unsupported", 0.44),
            ]
        )
        checker = _checker(
            _context(provider=provider, knowledge_service=_knowledge((_hit(),)))
        )

        claims = await checker.check(
            deliverable_content=_NUMERIC_DELIVERABLE,
            execution_id=_EXEC,
            project_id=_PROJECT,
        )

        assert claims == ()


class TestSearchLimitGuard:
    def test_non_positive_search_limit_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="search_limit"):
            KnowledgeSubstrateGroundingChecker(resolver=lambda: None, search_limit=0)
