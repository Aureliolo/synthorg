"""Service-level tests for the research pipeline and its replayability.

These exercise the whole orchestration with structural fakes (no real
network or backend), including the deterministic replay path where the
persisted run's items are served back via :class:`ReplayRetrievalSource`.
"""

import json
from datetime import UTC, datetime

import pytest
from tests._shared import FakeClock
from tests._shared.scripted_provider import ScriptedProvider
from tests.unit.research._fakes import (
    FakeWebSearchProvider,
    InMemoryResearchRunRepository,
    scripted_response,
)

from synthorg.core.enums import ResearchRunStatus, ResearchSourceType
from synthorg.research.errors import ResearchRunError
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

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 22, tzinfo=UTC)

_PLAN = json.dumps(
    {
        "research_angle": "state of the art",
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
            },
            {
                "ref_id": "src-0-1",
                "authority": "community",
                "domain_alignment": 0.7,
                "score": 0.75,
                "red_flags": [],
            },
        ]
    }
)
_SYNTH = json.dumps(
    {
        "title": "Widgets Today",
        "summary": "A concise overview of the widget landscape and benchmarks.",
        "claims": [
            {
                "text": "Widgets are widely benchmarked.",
                "claim_type": "fact",
                "confidence": 0.9,
                "ref_ids": ["src-0-0"],
            }
        ],
    }
)


def _brief() -> ResearchBrief:
    return ResearchBrief(
        brief_id="b1",
        title="Widget research",
        question="widget performance benchmarks",
        include_knowledge=False,
        include_web=True,
        created_at=_NOW,
    )


def _web_provider() -> FakeWebSearchProvider:
    return FakeWebSearchProvider(
        [
            SearchResult(
                title="Benchmark study",
                url="https://study.example/widgets",
                snippet="widget performance benchmarks across vendors",
            ),
            SearchResult(
                title="Vendor guide",
                url="https://guide.example/widgets",
                snippet="widget selection and performance guidance",
            ),
        ]
    )


def _build_service(
    provider: ScriptedProvider,
    sources: dict[ResearchSourceType, RetrievalSource],
) -> ResearchService:
    return ResearchService(
        planner=LlmQueryPlanner(provider=provider, model="m"),
        sources=sources,
        triage=HybridCredibilityTriage(
            heuristic=HeuristicCredibilityTriage(clock=FakeClock(start=_NOW)),
            llm=LlmCredibilityTriage(provider=provider, model="m"),
        ),
        deduplicator=LexicalDeduplicator(),
        synthesizer=LlmSynthesizer(
            provider=provider,
            model="example-medium-001",
            binder=CitationBinder(),
            clock=FakeClock(start=_NOW),
        ),
        runs_repo=InMemoryResearchRunRepository(),
        clock=FakeClock(start=_NOW),
    )


async def test_pipeline_produces_cited_report() -> None:
    provider = ScriptedProvider(
        responses=[
            scripted_response(_PLAN),
            scripted_response(_TRIAGE),
            scripted_response(_SYNTH),
        ]
    )
    web = WebRetrievalSource(provider=_web_provider(), clock=FakeClock(start=_NOW))
    service = _build_service(provider, {ResearchSourceType.WEB: web})

    run = await service.run(_brief(), run_id="run-1", created_by="agent-1")

    assert run.status is ResearchRunStatus.COMPLETED
    assert run.report is not None
    assert run.report.claims
    # Every claim citation resolves to a retrieved item.
    retrieved = {item.ref_id for item in run.retrieved_items}
    for claim in run.report.claims:
        for citation in claim.citations:
            assert citation.ref_id in retrieved
    assert run.cost > 0.0


async def test_run_is_replayable_byte_identical() -> None:
    record_provider = ScriptedProvider(
        responses=[
            scripted_response(_PLAN),
            scripted_response(_TRIAGE),
            scripted_response(_SYNTH),
        ]
    )
    web = WebRetrievalSource(provider=_web_provider(), clock=FakeClock(start=_NOW))
    recorded = await _build_service(record_provider, {ResearchSourceType.WEB: web}).run(
        _brief(), run_id="run-1", created_by="agent-1"
    )

    # Replay: same scripted LLM responses, retrieval served from the run.
    replay_provider = ScriptedProvider(
        responses=[
            scripted_response(_PLAN),
            scripted_response(_TRIAGE),
            scripted_response(_SYNTH),
        ]
    )
    replay_web = _web_provider()
    replay_sources = build_replay_sources(recorded.retrieved_items)
    replayed = await _build_service(replay_provider, dict(replay_sources)).run(
        _brief(), run_id="run-1", created_by="agent-1"
    )

    assert replayed.report is not None
    assert recorded.report is not None
    # Every pipeline stage reproduces identically, not just the final report.
    assert replayed.retrieved_items == recorded.retrieved_items
    assert replayed.query_plan is not None
    assert recorded.query_plan is not None
    assert (
        replayed.query_plan.model_dump_json() == recorded.query_plan.model_dump_json()
    )
    assert replayed.credibility == recorded.credibility
    assert replayed.report.model_dump_json() == recorded.report.model_dump_json()
    # Replay did not touch the real web provider.
    assert replay_web.queries == []


async def test_run_persists_failure_and_raises() -> None:
    provider = ScriptedProvider(response=scripted_response("not json"))
    web = WebRetrievalSource(provider=_web_provider(), clock=FakeClock(start=_NOW))
    repo = InMemoryResearchRunRepository()
    service = ResearchService(
        planner=LlmQueryPlanner(provider=provider, model="m"),
        sources={ResearchSourceType.WEB: web},
        triage=HeuristicCredibilityTriage(clock=FakeClock(start=_NOW)),
        deduplicator=LexicalDeduplicator(),
        synthesizer=LlmSynthesizer(
            provider=provider,
            model="m",
            binder=CitationBinder(),
            clock=FakeClock(start=_NOW),
        ),
        runs_repo=repo,
        clock=FakeClock(start=_NOW),
    )

    with pytest.raises(ResearchRunError):
        await service.run(_brief(), run_id="run-x", created_by="agent")

    stored = await repo.get("run-x")
    assert stored is not None
    assert stored.status is ResearchRunStatus.FAILED
    assert stored.error is not None
