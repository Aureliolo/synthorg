"""Research eval lane: run a research brief, replay it, and grade it.

This is the #1989 acceptance under the eval harness: given a research
brief the org produces a synthesised, citation-backed report whose claims
resolve to retrievable sources, and the run is replayable. The lane drives
:class:`ResearchService` with a scripted provider (standing in for the
cassette), records the run, replays it from the persisted items, asserts
the report is byte-identical, and grades the run with the deterministic
research grader.
"""

import json
from datetime import UTC, datetime

import pytest

from evals.models.brief import (
    Brief,
    BriefKind,
    BriefPriority,
    JudgedRubric,
    LimitsSpec,
    ResearchBriefSpec,
    RubricDimension,
    RubricGradeType,
)
from evals.scoring.research import grade_research_run
from synthorg.core.enums import ResearchSourceType
from synthorg.core.types import NotBlankStr
from synthorg.research.models import ResearchBrief
from synthorg.research.planning.llm_planner import LlmQueryPlanner
from synthorg.research.retrieval.dedup import LexicalDeduplicator
from synthorg.research.retrieval.protocol import RetrievalSource
from synthorg.research.retrieval.replay import build_replay_sources
from synthorg.research.retrieval.sources.web import WebRetrievalSource
from synthorg.research.service import ResearchService
from synthorg.research.synthesis.citation_binder import CitationBinder
from synthorg.research.synthesis.llm_synthesizer import LlmSynthesizer
from synthorg.research.triage.heuristic import HeuristicCredibilityTriage
from synthorg.research.triage.hybrid import HybridCredibilityTriage
from synthorg.research.triage.llm import LlmCredibilityTriage
from synthorg.tools.web.web_search import SearchResult
from tests._shared import FakeClock
from tests._shared.scripted_provider import ScriptedProvider
from tests.unit.research._fakes import (
    FakeWebSearchProvider,
    InMemoryResearchRunRepository,
    scripted_response,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 22, tzinfo=UTC)

_PLAN = json.dumps(
    {
        "research_angle": "adoption",
        "sub_queries": [
            {"source_type": "web", "query_text": "widget benchmarks", "intent": "core"}
        ],
    }
)
_TRIAGE = json.dumps(
    {
        "verdicts": [
            {
                "ref_id": "src-0-0",
                "authority": "community",
                "domain_alignment": 0.8,
                "score": 0.9,
                "red_flags": [],
            }
        ]
    }
)
_SYNTH = json.dumps(
    {
        "title": "Widgets Today",
        "summary": "A concise overview of widget adoption and benchmarks.",
        "claims": [
            {
                "text": "Widgets are widely benchmarked across vendors.",
                "claim_type": "fact",
                "confidence": 0.9,
                "ref_ids": ["src-0-0"],
            }
        ],
    }
)


def _research_spec() -> ResearchBriefSpec:
    return ResearchBriefSpec(
        question=NotBlankStr("how widely are widgets benchmarked?"),
        expected_claims=(
            NotBlankStr("Widgets are widely benchmarked across vendors."),
        ),
        min_credibility=0.5,
        rubric=JudgedRubric(
            rubric_id=NotBlankStr("research-widgets"),
            dimensions=(
                RubricDimension(
                    name=NotBlankStr("accuracy"),
                    weight=1.0,
                    grade_type=RubricGradeType.SCORE,
                ),
            ),
            reference_answer_path=NotBlankStr("evals/refs/widgets.md"),
        ),
    )


def _exam_brief() -> Brief:
    return Brief(
        brief_id=NotBlankStr("research-widgets-001"),
        schema_version=1,
        kind=BriefKind.RESEARCH,
        title=NotBlankStr("Widget benchmarking survey"),
        description=NotBlankStr("Survey how widely widgets are benchmarked."),
        priority=BriefPriority.MEDIUM,
        estimated_complexity=2,
        acceptance_criteria=(NotBlankStr("Report cites retrievable sources."),),
        limits=LimitsSpec(
            max_total_cost_usd=10.0, max_wall_clock_seconds=120, max_turns=10
        ),
        research_spec=_research_spec(),
    )


def _scripted() -> ScriptedProvider:
    return ScriptedProvider(
        responses=[
            scripted_response(_PLAN),
            scripted_response(_TRIAGE),
            scripted_response(_SYNTH),
        ]
    )


def _build_service(
    provider: ScriptedProvider,
    sources: dict[ResearchSourceType, RetrievalSource],
) -> ResearchService:
    clock = FakeClock(start=_NOW)
    return ResearchService(
        planner=LlmQueryPlanner(provider=provider, model="m"),
        sources=sources,
        triage=HybridCredibilityTriage(
            heuristic=HeuristicCredibilityTriage(clock=clock),
            llm=LlmCredibilityTriage(provider=provider, model="m"),
        ),
        deduplicator=LexicalDeduplicator(),
        synthesizer=LlmSynthesizer(
            provider=provider,
            model="example-medium-001",
            binder=CitationBinder(),
            clock=clock,
        ),
        runs_repo=InMemoryResearchRunRepository(),
        clock=clock,
    )


def _runtime_brief() -> ResearchBrief:
    return ResearchBrief(
        brief_id=NotBlankStr("research-widgets-001"),
        title=NotBlankStr("Widget benchmarking survey"),
        question=NotBlankStr("how widely are widgets benchmarked?"),
        include_knowledge=False,
        include_web=True,
        created_at=_NOW,
    )


async def test_research_lane_runs_replays_and_grades() -> None:
    exam = _exam_brief()
    assert exam.kind is BriefKind.RESEARCH
    assert exam.research_spec is not None

    web = FakeWebSearchProvider(
        [
            SearchResult(
                title="Benchmark study",
                url="https://study.example/widgets",
                snippet="widgets are widely benchmarked across vendors",
            )
        ]
    )
    record_sources: dict[ResearchSourceType, RetrievalSource] = {
        ResearchSourceType.WEB: WebRetrievalSource(
            provider=web, clock=FakeClock(start=_NOW)
        )
    }
    recorded = await _build_service(_scripted(), record_sources).run(
        _runtime_brief(), run_id=NotBlankStr("run-1"), created_by=NotBlankStr("agent")
    )

    # Replay: retrieval served from the recorded run; the real web provider
    # is not touched.
    replay_web = FakeWebSearchProvider([])
    replay_sources: dict[ResearchSourceType, RetrievalSource] = dict(
        build_replay_sources(recorded.retrieved_items)
    )
    replayed = await _build_service(_scripted(), replay_sources).run(
        _runtime_brief(), run_id=NotBlankStr("run-1"), created_by=NotBlankStr("agent")
    )

    assert recorded.report is not None
    assert replayed.report is not None
    assert replayed.report.model_dump_json() == recorded.report.model_dump_json()
    assert replay_web.queries == []

    score = grade_research_run(recorded, exam.research_spec)
    assert score.citation_resolution == 1.0
    assert score.claim_coverage == 1.0
    assert score.passed is True
