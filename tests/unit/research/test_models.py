"""Unit tests for :mod:`synthorg.research.models`.

Covers the structural invariants the research pipeline relies on: frozen +
extra-forbid, the citation knowledge/external XOR, item-to-citation
linkage, claim citation requirement, report count invariant, run
consistency invariants, and JSON round-trip shape.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.core.enums import (
    ClaimType,
    ResearchRunStatus,
    ResearchSourceType,
    SourceType,
)
from synthorg.knowledge.models import Citation, WebLocator
from synthorg.research.models import (
    AcademicSourceLocator,
    CodeSourceLocator,
    ResearchBrief,
    ResearchCitation,
    ResearchClaim,
    ResearchQueryPlan,
    ResearchReport,
    ResearchRun,
    RetrievedItem,
    SourceCredibility,
    SubQuery,
    WebSourceLocator,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 22, tzinfo=UTC)
_HASH = "a" * 64


def _brief(**overrides: object) -> ResearchBrief:
    base: dict[str, object] = {
        "brief_id": "b1",
        "title": "Survey of widgets",
        "question": "What is the state of widgets?",
        "created_at": _NOW,
    }
    base.update(overrides)
    return ResearchBrief(**base)  # type: ignore[arg-type]


def _web_citation(ref_id: str = "src-1") -> ResearchCitation:
    return ResearchCitation(
        ref_id=ref_id,
        source_type=ResearchSourceType.WEB,
        external=WebSourceLocator(url="https://example.org/a", accessed_at=_NOW),
    )


def _knowledge_citation(ref_id: str = "src-2") -> ResearchCitation:
    citation = Citation(
        source_id="ks1",
        chunk_id="ck1",
        source_type=SourceType.WEB,
        title="Doc",
        uri="https://example.org/doc",
        locator=WebLocator(url="https://example.org/doc", char_start=0, char_end=10),
        content_hash=_HASH,
    )
    return ResearchCitation(
        ref_id=ref_id,
        source_type=ResearchSourceType.KNOWLEDGE,
        knowledge=citation,
    )


def _item(ref_id: str = "src-1", **overrides: object) -> RetrievedItem:
    base: dict[str, object] = {
        "ref_id": ref_id,
        "sub_query_index": 0,
        "source_type": ResearchSourceType.WEB,
        "title": "A page",
        "uri": "https://example.org/a",
        "snippet": "some retrieved text",
        "content_hash": _HASH,
        "relevance_score": 0.5,
        "citation": _web_citation(ref_id),
    }
    base.update(overrides)
    return RetrievedItem(**base)  # type: ignore[arg-type]


def _report(**overrides: object) -> ResearchReport:
    base: dict[str, object] = {
        "report_id": "r1",
        "brief_id": "b1",
        "title": "Report",
        "summary": "An executive summary.",
        "claims": (
            ResearchClaim(
                claim_id="c1",
                text="Widgets are widely adopted.",
                claim_type=ClaimType.FACT,
                citations=(_web_citation(),),
                confidence=0.8,
            ),
        ),
        "sources_consulted": 3,
        "sources_retained": 1,
        "research_angle": "adoption",
        "synthesis_model": "example-medium-001",
        "created_at": _NOW,
    }
    base.update(overrides)
    return ResearchReport(**base)  # type: ignore[arg-type]


# ── Brief ────────────────────────────────────────────────────────────


def test_brief_enabled_source_types_fixed_order() -> None:
    brief = _brief(include_academic=True, include_code=True)
    assert brief.enabled_source_types == (
        ResearchSourceType.KNOWLEDGE,
        ResearchSourceType.WEB,
        ResearchSourceType.ACADEMIC,
        ResearchSourceType.CODE,
    )


def test_brief_rejects_no_enabled_source() -> None:
    with pytest.raises(ValidationError, match="at least one retrieval source"):
        _brief(include_knowledge=False, include_web=False)


def test_brief_is_frozen() -> None:
    brief = _brief()
    with pytest.raises(ValidationError):
        brief.title = "mutated"  # type: ignore[misc]


def test_brief_rejects_subquery_ceiling_breach() -> None:
    with pytest.raises(ValidationError):
        _brief(max_subqueries=10_000)


# ── Query plan ───────────────────────────────────────────────────────


def test_query_plan_rejects_duplicate_indices() -> None:
    sq0 = SubQuery(
        index=0,
        source_type=ResearchSourceType.WEB,
        query_text="a",
        intent="why",
    )
    sq1 = SubQuery(
        index=0,
        source_type=ResearchSourceType.KNOWLEDGE,
        query_text="b",
        intent="why",
    )
    with pytest.raises(ValidationError, match="indices must be unique"):
        ResearchQueryPlan(brief_id="b1", research_angle="x", sub_queries=(sq0, sq1))


def test_query_plan_requires_at_least_one_subquery() -> None:
    with pytest.raises(ValidationError):
        ResearchQueryPlan(brief_id="b1", research_angle="x", sub_queries=())


# ── Citation ─────────────────────────────────────────────────────────


def test_citation_knowledge_requires_knowledge_payload() -> None:
    with pytest.raises(ValidationError, match="knowledge citation requires"):
        ResearchCitation(
            ref_id="src-1",
            source_type=ResearchSourceType.KNOWLEDGE,
            external=WebSourceLocator(url="https://x", accessed_at=_NOW),
        )


def test_citation_external_requires_external_payload() -> None:
    with pytest.raises(ValidationError, match="external citation requires"):
        ResearchCitation(ref_id="src-1", source_type=ResearchSourceType.WEB)


def test_citation_external_kind_must_match_source_type() -> None:
    with pytest.raises(ValidationError, match="does not match citation source_type"):
        ResearchCitation(
            ref_id="src-1",
            source_type=ResearchSourceType.ACADEMIC,
            external=WebSourceLocator(url="https://x", accessed_at=_NOW),
        )


def test_citation_academic_and_code_locators() -> None:
    academic = ResearchCitation(
        ref_id="src-3",
        source_type=ResearchSourceType.ACADEMIC,
        external=AcademicSourceLocator(identifier="arXiv:1234", year=2024),
    )
    code = ResearchCitation(
        ref_id="src-4",
        source_type=ResearchSourceType.CODE,
        external=CodeSourceLocator(repo="owner/repo", path="a.py", line_start=1),
    )
    assert academic.external is not None
    assert code.external is not None


def test_code_locator_rejects_inverted_line_range() -> None:
    with pytest.raises(ValidationError, match="line_end"):
        CodeSourceLocator(repo="o/r", path="a.py", line_start=10, line_end=2)


# ── Retrieved item ───────────────────────────────────────────────────


def test_item_rejects_citation_ref_mismatch() -> None:
    with pytest.raises(ValidationError, match="does not match item"):
        _item(ref_id="src-1", citation=_web_citation("src-9"))


def test_item_rejects_citation_source_type_mismatch() -> None:
    with pytest.raises(ValidationError, match="does not match item source_type"):
        _item(source_type=ResearchSourceType.ACADEMIC)


def test_item_rejects_non_hex_content_hash() -> None:
    with pytest.raises(ValidationError):
        _item(content_hash="not-a-hash")


# ── Claim / report ───────────────────────────────────────────────────


def test_claim_requires_at_least_one_citation() -> None:
    with pytest.raises(ValidationError):
        ResearchClaim(
            claim_id="c1",
            text="unsourced",
            claim_type=ClaimType.ANALYSIS,
            citations=(),
            confidence=0.1,
        )


def test_report_rejects_retained_exceeding_consulted() -> None:
    with pytest.raises(ValidationError, match="cannot exceed"):
        _report(sources_consulted=1, sources_retained=2)


# ── Run ──────────────────────────────────────────────────────────────


def test_run_rejects_brief_id_mismatch() -> None:
    with pytest.raises(ValidationError, match="does not match run brief_id"):
        ResearchRun(
            run_id="run1",
            brief_id="other",
            status=ResearchRunStatus.PLANNING,
            brief=_brief(),
            created_by="agent",
            created_at=_NOW,
        )


def test_run_rejects_project_id_mismatch() -> None:
    with pytest.raises(ValidationError, match="project_id"):
        ResearchRun(
            run_id="run1",
            brief_id="b1",
            project_id="p-other",
            status=ResearchRunStatus.PLANNING,
            brief=_brief(),
            created_by="agent",
            created_at=_NOW,
        )


def test_run_completed_requires_report_and_completed_at() -> None:
    with pytest.raises(ValidationError, match="status=COMPLETED requires"):
        ResearchRun(
            run_id="run1",
            brief_id="b1",
            status=ResearchRunStatus.COMPLETED,
            brief=_brief(),
            created_by="agent",
            created_at=_NOW,
        )


def test_run_failed_requires_error() -> None:
    with pytest.raises(ValidationError, match="status=FAILED requires"):
        ResearchRun(
            run_id="run1",
            brief_id="b1",
            status=ResearchRunStatus.FAILED,
            brief=_brief(),
            created_by="agent",
            created_at=_NOW,
        )


def test_run_completed_round_trips_through_json() -> None:
    run = ResearchRun(
        run_id="run1",
        brief_id="b1",
        status=ResearchRunStatus.COMPLETED,
        brief=_brief(),
        retrieved_items=(
            _item(),
            _item(
                ref_id="src-2",
                source_type=ResearchSourceType.KNOWLEDGE,
                citation=_knowledge_citation("src-2"),
            ),
        ),
        credibility=(
            SourceCredibility(
                ref_id="src-1",
                score=0.7,
                authority="published",
                domain_alignment=0.6,
                passed=True,
            ),
        ),
        report=_report(),
        completed_at=_NOW,
        created_by="agent",
        created_at=_NOW,
    )
    restored = ResearchRun.model_validate_json(run.model_dump_json())
    assert restored == run
    assert restored.retrieved_items[1].citation.knowledge is not None
