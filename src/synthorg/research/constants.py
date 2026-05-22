"""Module-level named constants for the research subsystem.

Numeric tuning knobs live here in annotated ``Final`` form so the
``scripts/check_no_magic_numbers.py`` gate (which allow-lists the
``NAME: Final[...] = literal`` pattern) passes and every knob has a single
home. Operator-facing strategy discriminators live on
:class:`~synthorg.research.config.ResearchConfig`; the brief-level limits
default to the constants below.
"""

from typing import Final

from synthorg.core.types import NotBlankStr

RESEARCH_DEFAULT_MAX_SUBQUERIES: Final[int] = 8
"""Default ceiling on the number of sub-queries a planner may emit."""

RESEARCH_MAX_SUBQUERIES_CEILING: Final[int] = 32
"""Hard upper bound a brief may request for its sub-query budget; bounds
planner fan-out latency and cost."""

RESEARCH_DEFAULT_PER_QUERY_LIMIT: Final[int] = 10
"""Default number of candidate items each retrieval source returns per
sub-query."""

RESEARCH_DEFAULT_MIN_CREDIBILITY: Final[float] = 0.5
"""Default minimum credibility score a source must reach to be retained."""

RESEARCH_DEFAULT_MAX_COST_USD: Final[float] = 50.0
"""Default per-run cost ceiling in USD."""

RESEARCH_DEFAULT_MAX_WALL_CLOCK_SECONDS: Final[int] = 300
"""Default per-run wall-clock ceiling in seconds."""

RESEARCH_TRIAGE_BATCH_SIZE: Final[int] = 10
"""Number of retrieved items scored per LLM credibility-triage call, to
keep each request inside a comfortable token budget."""

RESEARCH_DEDUP_JACCARD_THRESHOLD: Final[float] = 0.85
"""Token-shingle Jaccard similarity at or above which two items are
treated as near-duplicates by the lexical deduplicator."""

RESEARCH_DEDUP_SHINGLE_SIZE: Final[int] = 3
"""Word-shingle width used to build the token-shingle sets for the lexical
deduplicator's Jaccard comparison."""

RESEARCH_AUTHORITY_ACADEMIC: Final[float] = 0.85
"""Heuristic base authority for academic (peer-reviewed) sources."""

RESEARCH_AUTHORITY_KNOWLEDGE: Final[float] = 0.7
"""Heuristic base authority for the internal (vetted) knowledge substrate."""

RESEARCH_AUTHORITY_CODE: Final[float] = 0.6
"""Heuristic base authority for code sources."""

RESEARCH_AUTHORITY_WEB: Final[float] = 0.5
"""Heuristic base authority for open-web sources."""

RESEARCH_HEURISTIC_AUTHORITY_WEIGHT: Final[float] = 0.5
"""Weight of the authority term in the heuristic credibility score."""

RESEARCH_HEURISTIC_ALIGNMENT_WEIGHT: Final[float] = 0.3
"""Weight of the topic-alignment term in the heuristic credibility score."""

RESEARCH_HEURISTIC_RECENCY_WEIGHT: Final[float] = 0.2
"""Weight of the recency term in the heuristic credibility score."""

RESEARCH_HEURISTIC_RED_FLAG_PENALTY: Final[float] = 0.25
"""Score deducted per detected low-quality red flag."""

RESEARCH_RECENCY_NEUTRAL_CREDIT: Final[float] = 0.5
"""Recency credit assigned when a source's age is unknown (neither rewarded
nor penalised)."""

RESEARCH_HYBRID_PREFILTER_FACTOR: Final[float] = 0.6
"""Fraction of the brief threshold a source's heuristic score must reach to
be escalated to LLM triage in the hybrid strategy; weaker sources keep
their cheap heuristic verdict."""

RESEARCH_HEURISTIC_RECENCY_FULL_MONTHS: Final[int] = 12
"""Sources at most this many months old score full recency credit; older
sources decay linearly to zero over the horizon below."""

RESEARCH_HEURISTIC_RECENCY_HORIZON_MONTHS: Final[int] = 60
"""Age beyond which a source contributes no recency credit."""

RESEARCH_LIST_DEFAULT_LIMIT: Final[int] = 100
"""Default page size when listing research runs."""

RESEARCH_LIST_MAX_LIMIT: Final[int] = 500
"""Maximum page size accepted when listing research runs, bounding latency."""

RESEARCH_SNIPPET_MAX_CHARS: Final[int] = 8192
"""Upper bound on a retrieved-item snippet, matching ``SnippetText``; longer
source text (e.g. a full knowledge chunk) is truncated to this length."""

RESEARCH_REF_ID_PREFIX: Final[NotBlankStr] = NotBlankStr("src")
"""Prefix for the stable per-item reference identifier the synthesiser
cites (e.g. ``src-1``); the citation binder resolves these back to items."""
