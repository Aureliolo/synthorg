"""Unit tests for credibility triage (heuristic, LLM, hybrid)."""

import json
from datetime import UTC, datetime

import pytest

from synthorg.core.enums import ResearchSourceType
from synthorg.research.models import (
    AcademicSourceLocator,
    ResearchBrief,
    ResearchCitation,
    RetrievedItem,
    WebSourceLocator,
)
from synthorg.research.triage.heuristic import HeuristicCredibilityTriage
from synthorg.research.triage.hybrid import HybridCredibilityTriage
from synthorg.research.triage.llm import LlmCredibilityTriage
from tests._shared import FakeClock
from tests._shared.scripted_provider import ScriptedProvider
from tests.unit.research._fakes import scripted_response

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 22, tzinfo=UTC)
_HASH = "c" * 64


def _brief(min_credibility: float = 0.5) -> ResearchBrief:
    return ResearchBrief(
        brief_id="b1",
        title="Widget benchmarks",
        question="widget performance benchmarks",
        min_credibility=min_credibility,
        created_at=_NOW,
    )


def _academic_item(ref_id: str, *, snippet: str, year: int) -> RetrievedItem:
    return RetrievedItem(
        ref_id=ref_id,
        sub_query_index=0,
        source_type=ResearchSourceType.ACADEMIC,
        title="Widget paper",
        uri="arXiv:1",
        snippet=snippet,
        content_hash=_HASH,
        relevance_score=0.9,
        citation=ResearchCitation(
            ref_id=ref_id,
            source_type=ResearchSourceType.ACADEMIC,
            external=AcademicSourceLocator(identifier="arXiv:1", year=year),
        ),
    )


def _web_item(ref_id: str, *, snippet: str) -> RetrievedItem:
    return RetrievedItem(
        ref_id=ref_id,
        sub_query_index=0,
        source_type=ResearchSourceType.WEB,
        title="Blog",
        uri="https://blog.example",
        snippet=snippet,
        content_hash="d" * 64,
        relevance_score=0.5,
        citation=ResearchCitation(
            ref_id=ref_id,
            source_type=ResearchSourceType.WEB,
            external=WebSourceLocator(url="https://blog.example", accessed_at=_NOW),
        ),
    )


# ── Heuristic ────────────────────────────────────────────────────────


async def test_heuristic_passes_recent_on_topic_academic() -> None:
    triage = HeuristicCredibilityTriage(clock=FakeClock(start=_NOW))
    item = _academic_item("src-0-0", snippet="widget performance benchmarks", year=2025)

    (verdict,), cost = await triage.triage((item,), brief=_brief())

    assert cost == 0.0
    assert verdict.authority == "peer_reviewed"
    assert verdict.recency_months == 12
    assert verdict.passed is True


async def test_heuristic_flags_marketing_and_fails_threshold() -> None:
    triage = HeuristicCredibilityTriage(clock=FakeClock(start=_NOW))
    item = _web_item("src-0-0", snippet="buy now! limited offer with discount code")

    (verdict,), _ = await triage.triage((item,), brief=_brief())

    assert verdict.red_flags
    assert verdict.passed is False


# ── LLM ──────────────────────────────────────────────────────────────


async def test_llm_triage_parses_verdicts_and_defaults_missing() -> None:
    items = (
        _web_item("src-0-0", snippet="alpha"),
        _web_item("src-0-1", snippet="beta"),
    )
    payload = json.dumps(
        {
            "verdicts": [
                {
                    "ref_id": "src-0-0",
                    "authority": "community",
                    "domain_alignment": 0.7,
                    "score": 0.8,
                    "red_flags": [],
                }
            ]
        }
    )
    provider = ScriptedProvider(response=scripted_response(payload, cost=0.05))
    triage = LlmCredibilityTriage(provider=provider, model="m")

    verdicts, cost = await triage.triage(items, brief=_brief())

    assert cost == 0.05
    scored = {v.ref_id: v for v in verdicts}
    assert scored["src-0-0"].score == 0.8
    assert scored["src-0-0"].passed is True
    assert scored["src-0-1"].score == 0.0
    assert scored["src-0-1"].passed is False


# ── Hybrid ───────────────────────────────────────────────────────────


async def test_hybrid_escalates_only_survivors() -> None:
    good = _academic_item("src-0-0", snippet="widget performance benchmarks", year=2025)
    bad = _web_item("src-0-1", snippet="buy now discount code unrelated")
    payload = json.dumps(
        {
            "verdicts": [
                {
                    "ref_id": "src-0-0",
                    "authority": "peer_reviewed",
                    "domain_alignment": 0.9,
                    "score": 0.95,
                    "red_flags": [],
                }
            ]
        }
    )
    provider = ScriptedProvider(response=scripted_response(payload, cost=0.03))
    hybrid = HybridCredibilityTriage(
        heuristic=HeuristicCredibilityTriage(clock=FakeClock(start=_NOW)),
        llm=LlmCredibilityTriage(provider=provider, model="m"),
    )

    verdicts, cost = await hybrid.triage((good, bad), brief=_brief())

    by_ref = {v.ref_id: v for v in verdicts}
    assert cost == 0.03
    assert by_ref["src-0-0"].score == 0.95
    assert by_ref["src-0-1"].passed is False
    assert provider.call_count == 1
