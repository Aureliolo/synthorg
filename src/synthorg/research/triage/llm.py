"""LLM-backed credibility triage.

Scores items in deterministic batches: each batch presents the item
metadata (with untrusted snippets wrapped) and asks the model to rate
authority, topic alignment, and red flags. Verdicts are matched back to
items by ``ref_id``; any item the model omits gets a conservative
zero-score verdict so it cannot silently pass triage.
"""

import json
from typing import TYPE_CHECKING, Final

from pydantic import ValidationError

from synthorg.api.boundary import parse_typed
from synthorg.engine.prompt_safety import (
    TAG_RESEARCH_SOURCE,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.research import RESEARCH_LLM_OUTPUT_INVALID
from synthorg.research._args import TriageOutput, TriageVerdictOut
from synthorg.research._llm import complete_text, extract_json_object
from synthorg.research.constants import RESEARCH_TRIAGE_BATCH_SIZE
from synthorg.research.errors import ResearchRunError
from synthorg.research.models import ResearchBrief, RetrievedItem, SourceCredibility

if TYPE_CHECKING:
    from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)

_TRIAGE_BOUNDARY: Final[str] = "research.triage"

_SYSTEM_PROMPT: Final[str] = (
    "You are a source-credibility analyst. For each numbered source, judge "
    "its credibility for the research question. Return ONLY a JSON object:\n"
    '{"verdicts": [{"ref_id": "<id>", "authority": "<peer_reviewed|expert|'
    'published|community|unknown>", "domain_alignment": <0..1>, '
    '"score": <0..1>, "red_flags": ["<flag>"]}]}\n'
    "Score lower for marketing material, unverified claims, or off-topic "
    "sources. Include one verdict per source, keyed by its exact ref_id. "
    + untrusted_content_directive((TAG_RESEARCH_SOURCE,))
)


class LlmCredibilityTriage:
    """Scores credibility with batched deterministic LLM calls."""

    __slots__ = ("_batch_size", "_model", "_provider")

    def __init__(
        self,
        *,
        provider: CompletionProvider,
        model: str,
        batch_size: int = RESEARCH_TRIAGE_BATCH_SIZE,
    ) -> None:
        self._provider = provider
        self._model = model
        self._batch_size = batch_size

    async def triage(
        self,
        items: tuple[RetrievedItem, ...],
        *,
        brief: ResearchBrief,
    ) -> tuple[tuple[SourceCredibility, ...], float]:
        """Return one verdict per item and the accrued LLM cost."""
        verdicts_by_ref: dict[str, TriageVerdictOut] = {}
        total_cost = 0.0
        for start in range(0, len(items), self._batch_size):
            batch = items[start : start + self._batch_size]
            output, cost = await self._score_batch(batch, brief=brief)
            total_cost += cost
            for verdict in output.verdicts:
                verdicts_by_ref[verdict.ref_id] = verdict
        results = tuple(
            _to_credibility(item, verdicts_by_ref.get(item.ref_id), brief=brief)
            for item in items
        )
        return results, total_cost

    async def _score_batch(
        self,
        batch: tuple[RetrievedItem, ...],
        *,
        brief: ResearchBrief,
    ) -> tuple[TriageOutput, float]:
        """Score one batch and parse the model's structured verdicts."""
        blocks = [
            f"ref_id: {item.ref_id}\nsource_type: {item.source_type.value}\n"
            f"title: {item.title}\n"
            f"{wrap_untrusted(TAG_RESEARCH_SOURCE, item.snippet)}"
            for item in batch
        ]
        user = f"Research question: {brief.question}\n\n" + "\n\n".join(blocks)
        content, cost = await complete_text(
            self._provider,
            self._model,
            system=_SYSTEM_PROMPT,
            user=user,
        )
        try:
            obj = json.loads(extract_json_object(content))
            return parse_typed(_TRIAGE_BOUNDARY, obj, TriageOutput), cost
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            logger.warning(
                RESEARCH_LLM_OUTPUT_INVALID,
                stage="triage",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Credibility triage returned unparseable output"
            raise ResearchRunError(msg) from exc


def _to_credibility(
    item: RetrievedItem,
    verdict: TriageVerdictOut | None,
    *,
    brief: ResearchBrief,
) -> SourceCredibility:
    """Build a verdict for an item, defaulting to zero when omitted."""
    if verdict is None:
        return SourceCredibility(
            ref_id=item.ref_id,
            score=0.0,
            authority="unknown",
            domain_alignment=0.0,
            red_flags=("not scored by triage",),
            passed=False,
        )
    return SourceCredibility(
        ref_id=item.ref_id,
        score=verdict.score,
        authority=verdict.authority,
        domain_alignment=verdict.domain_alignment,
        red_flags=verdict.red_flags,
        passed=verdict.score >= brief.min_credibility,
    )
