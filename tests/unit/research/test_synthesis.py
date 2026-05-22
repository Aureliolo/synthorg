"""Unit tests for synthesis: citation binder and LLM synthesiser."""

import json
from datetime import UTC, datetime

import pytest
from tests._shared import FakeClock
from tests._shared.scripted_provider import ScriptedProvider
from tests.unit.research._fakes import scripted_response

from synthorg.core.enums import ResearchSourceType
from synthorg.research.errors import ResearchSynthesisError
from synthorg.research.models import (
    ResearchBrief,
    ResearchCitation,
    ResearchQueryPlan,
    RetrievedItem,
    SubQuery,
    WebSourceLocator,
)
from synthorg.research.synthesis.citation_binder import CitationBinder
from synthorg.research.synthesis.llm_synthesizer import LlmSynthesizer

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 22, tzinfo=UTC)
_HASH = "c" * 64


def _item(ref_id: str) -> RetrievedItem:
    return RetrievedItem(
        ref_id=ref_id,
        sub_query_index=0,
        source_type=ResearchSourceType.WEB,
        title="Source",
        uri=f"https://src/{ref_id}",
        snippet=f"evidence for {ref_id}",
        content_hash=_HASH,
        relevance_score=0.7,
        citation=ResearchCitation(
            ref_id=ref_id,
            source_type=ResearchSourceType.WEB,
            external=WebSourceLocator(url=f"https://src/{ref_id}", accessed_at=_NOW),
        ),
    )


def _brief() -> ResearchBrief:
    return ResearchBrief(
        brief_id="b1",
        title="Widget report",
        question="what is the state of widgets?",
        created_at=_NOW,
    )


def _plan() -> ResearchQueryPlan:
    return ResearchQueryPlan(
        brief_id="b1",
        research_angle="state of the art",
        sub_queries=(
            SubQuery(
                index=0,
                source_type=ResearchSourceType.WEB,
                query_text="widgets",
                intent="probe",
            ),
        ),
    )


# ── Citation binder ──────────────────────────────────────────────────


def test_binder_resolves_and_dedupes() -> None:
    items = {"src-0-0": _item("src-0-0"), "src-0-1": _item("src-0-1")}
    citations = CitationBinder().resolve(("src-0-0", "src-0-0", "src-0-1"), items)
    assert [c.ref_id for c in citations] == ["src-0-0", "src-0-1"]


def test_binder_raises_on_unknown_ref() -> None:
    with pytest.raises(ResearchSynthesisError, match="unknown source"):
        CitationBinder().resolve(("src-9-9",), {"src-0-0": _item("src-0-0")})


def test_binder_raises_on_empty() -> None:
    with pytest.raises(ResearchSynthesisError, match="no sources"):
        CitationBinder().resolve((), {"src-0-0": _item("src-0-0")})


# ── LLM synthesiser ──────────────────────────────────────────────────


def _synth(provider: ScriptedProvider) -> LlmSynthesizer:
    return LlmSynthesizer(
        provider=provider,
        model="example-medium-001",
        binder=CitationBinder(),
        clock=FakeClock(start=_NOW),
    )


async def test_synthesiser_builds_cited_report() -> None:
    payload = json.dumps(
        {
            "title": "Widgets Today",
            "summary": "A concise overview of widgets.",
            "claims": [
                {
                    "text": "Widgets are widely adopted.",
                    "claim_type": "fact",
                    "confidence": 0.9,
                    "ref_ids": ["src-0-0"],
                }
            ],
        }
    )
    provider = ScriptedProvider(response=scripted_response(payload, cost=0.07))
    sources = (_item("src-0-0"), _item("src-0-1"))

    report, cost = await _synth(provider).synthesize(
        _brief(), _plan(), sources, sources_consulted=5
    )

    assert cost == 0.07
    assert report.report_id == "report-b1"
    assert report.sources_consulted == 5
    assert report.sources_retained == 2
    assert report.claims[0].citations[0].ref_id == "src-0-0"
    assert report.created_at == _NOW


async def test_synthesiser_rejects_no_sources() -> None:
    provider = ScriptedProvider(response=scripted_response("{}"))
    with pytest.raises(ResearchSynthesisError, match="no sources retained"):
        await _synth(provider).synthesize(_brief(), _plan(), (), sources_consulted=0)


async def test_synthesiser_rejects_claim_citing_unknown_source() -> None:
    payload = json.dumps(
        {
            "title": "Report",
            "summary": "Summary text.",
            "claims": [
                {
                    "text": "Unsourced claim.",
                    "claim_type": "analysis",
                    "confidence": 0.5,
                    "ref_ids": ["src-9-9"],
                }
            ],
        }
    )
    provider = ScriptedProvider(response=scripted_response(payload))
    with pytest.raises(ResearchSynthesisError, match="unknown source"):
        await _synth(provider).synthesize(
            _brief(), _plan(), (_item("src-0-0"),), sources_consulted=1
        )


async def test_synthesiser_rejects_unparseable_output() -> None:
    provider = ScriptedProvider(response=scripted_response("not json"))
    with pytest.raises(ResearchSynthesisError, match="unparseable"):
        await _synth(provider).synthesize(
            _brief(), _plan(), (_item("src-0-0"),), sources_consulted=1
        )
