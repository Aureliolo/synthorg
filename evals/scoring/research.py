"""Deterministic grading for ``kind=research`` briefs.

Scores a completed :class:`~synthorg.research.models.ResearchRun` against a
:class:`~evals.models.brief.ResearchBriefSpec` on three structural axes:

* **claim coverage** -- fraction of the brief's expected claims surfaced by
  the report (token-overlap match);
* **citation resolution** -- fraction of claim citations that resolve to a
  retrieved item in the run (the #1989 acceptance: every claim resolves to
  a retrievable source);
* **source credibility** -- fraction of cited sources whose triage score
  clears the brief's credibility floor.

These are deterministic (no LLM judge), so the lane is replay-stable: the
same recorded run grades identically every time.
"""

import re
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from evals.models.brief import ResearchBriefSpec
    from synthorg.research.models import ResearchRun

_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[^\W_]+", re.UNICODE)

COVERAGE_TOKEN_OVERLAP: Final[float] = 0.5
"""Fraction of an expected claim's tokens that must appear in a report claim
for the expected claim to count as covered."""


class ResearchScore(BaseModel):
    """Structural grade for one research run."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    claim_coverage: float = Field(ge=0.0, le=1.0)
    citation_resolution: float = Field(ge=0.0, le=1.0)
    source_credibility: float = Field(ge=0.0, le=1.0)
    overall: float = Field(ge=0.0, le=1.0)
    passed: bool


def _tokens(text: str) -> frozenset[str]:
    return frozenset(_TOKEN_RE.findall(text.casefold()))


def _coverage(run: ResearchRun, spec: ResearchBriefSpec) -> float:
    report = run.report
    if report is None:
        return 0.0
    claim_token_sets = [_tokens(claim.text) for claim in report.claims]
    covered = 0
    for expected in spec.expected_claims:
        wanted = _tokens(expected)
        if not wanted:
            continue
        if any(
            len(wanted & claim_tokens) / len(wanted) >= COVERAGE_TOKEN_OVERLAP
            for claim_tokens in claim_token_sets
        ):
            covered += 1
    return covered / len(spec.expected_claims)


def _citation_resolution(run: ResearchRun) -> float:
    report = run.report
    if report is None:
        return 0.0
    retrieved = {item.ref_id for item in run.retrieved_items}
    refs = [c.ref_id for claim in report.claims for c in claim.citations]
    if not refs:
        return 0.0
    return sum(1 for ref in refs if ref in retrieved) / len(refs)


def _source_credibility(run: ResearchRun, spec: ResearchBriefSpec) -> float:
    report = run.report
    if report is None:
        return 0.0
    cited = {c.ref_id for claim in report.claims for c in claim.citations}
    if not cited:
        return 0.0
    score_by_ref = {v.ref_id: v.score for v in run.credibility}
    clear = sum(
        1 for ref in cited if score_by_ref.get(ref, 0.0) >= spec.min_credibility
    )
    return clear / len(cited)


def grade_research_run(run: ResearchRun, spec: ResearchBriefSpec) -> ResearchScore:
    """Grade a completed research run against its brief spec.

    The run passes when every claim citation resolves to a retrieved source
    (the hard acceptance criterion); coverage and credibility are quality
    signals folded into the overall score.
    """
    coverage = _coverage(run, spec)
    resolution = _citation_resolution(run)
    credibility = _source_credibility(run, spec)
    overall = (coverage + resolution + credibility) / 3.0
    return ResearchScore(
        claim_coverage=coverage,
        citation_resolution=resolution,
        source_credibility=credibility,
        overall=overall,
        passed=resolution >= 1.0,
    )
