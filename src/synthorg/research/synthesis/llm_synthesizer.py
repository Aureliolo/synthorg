"""LLM-backed synthesiser.

Presents the retained sources (snippets wrapped as untrusted) to the model
and asks for a structured report whose claims cite sources by ``ref_id``.
The :class:`CitationBinder` then validates every cited reference resolves
to a retained item, so an emitted report is always citation-backed.
"""

import json
from typing import TYPE_CHECKING, Final

from pydantic import ValidationError

from synthorg.api.boundary import parse_typed
from synthorg.core.clock import Clock, SystemClock
from synthorg.engine.prompt_safety import (
    TAG_RESEARCH_SOURCE,
    TAG_TASK_DATA,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.research import RESEARCH_LLM_OUTPUT_INVALID
from synthorg.research._args import SynthesisOutput
from synthorg.research._llm import complete_text, extract_json_object
from synthorg.research.errors import ResearchSynthesisError
from synthorg.research.models import (
    ResearchBrief,
    ResearchClaim,
    ResearchQueryPlan,
    ResearchReport,
    RetrievedItem,
)

if TYPE_CHECKING:
    from synthorg.providers.protocol import CompletionProvider
    from synthorg.research.synthesis.citation_binder import CitationBinder

logger = get_logger(__name__)

_SYNTHESIS_BOUNDARY: Final[str] = "research.synthesis"

_SYSTEM_PROMPT: Final[str] = (
    "You are a research synthesiser. Using ONLY the provided sources, write a "
    "concise, well-structured report answering the research question. Every "
    "claim must cite one or more sources by their exact ref_id. Return ONLY a "
    "JSON object:\n"
    '{"title": "<report title>", "summary": "<executive summary>", '
    '"claims": [{"text": "<claim>", "claim_type": "<fact|analysis|'
    'recommendation|comparison>", "confidence": <0..1>, '
    '"ref_ids": ["<source ref_id>"]}]}\n'
    "Do not invent sources or cite a ref_id that is not listed. "
    + untrusted_content_directive((TAG_RESEARCH_SOURCE, TAG_TASK_DATA))
)


class LlmSynthesizer:
    """Produces a citation-backed report with one deterministic LLM call."""

    __slots__ = ("_binder", "_clock", "_model", "_provider")

    def __init__(
        self,
        *,
        provider: CompletionProvider,
        model: str,
        binder: CitationBinder,
        clock: Clock | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._binder = binder
        self._clock = clock if clock is not None else SystemClock()

    async def synthesize(
        self,
        brief: ResearchBrief,
        plan: ResearchQueryPlan,
        sources: tuple[RetrievedItem, ...],
        *,
        sources_consulted: int,
    ) -> tuple[ResearchReport, float]:
        """Return a cited report and the USD cost of producing it."""
        if not sources:
            msg = "no sources retained after triage; cannot synthesise a report"
            raise ResearchSynthesisError(msg)
        items_by_ref = {item.ref_id: item for item in sources}
        content, cost = await complete_text(
            self._provider,
            self._model,
            system=_SYSTEM_PROMPT,
            user=self._build_user_prompt(brief, plan, sources),
        )
        output = self._parse(content)
        claims = tuple(
            ResearchClaim(
                claim_id=f"claim-{index}",
                text=claim.text,
                claim_type=claim.claim_type,
                citations=self._binder.resolve(claim.ref_ids, items_by_ref),
                confidence=claim.confidence,
            )
            for index, claim in enumerate(output.claims)
        )
        report = ResearchReport(
            report_id=f"report-{brief.brief_id}",
            brief_id=brief.brief_id,
            title=output.title,
            summary=output.summary,
            claims=claims,
            sources_consulted=sources_consulted,
            sources_retained=len(sources),
            research_angle=plan.research_angle,
            synthesis_model=self._model,
            created_at=self._clock.now(),
        )
        return report, cost

    @staticmethod
    def _build_user_prompt(
        brief: ResearchBrief,
        plan: ResearchQueryPlan,
        sources: tuple[RetrievedItem, ...],
    ) -> str:
        """Build the user prompt: brief, angle, and wrapped source blocks.

        ``ref_id`` / ``source_type`` are trusted (assigned by the pipeline);
        the title, uri, and snippet all come from untrusted external
        sources, so they are wrapped together inside one fence.
        """
        question = wrap_untrusted(TAG_TASK_DATA, f"Question: {brief.question}")
        blocks = [
            f"ref_id: {item.ref_id}\nsource_type: {item.source_type.value}\n"
            + wrap_untrusted(
                TAG_RESEARCH_SOURCE,
                f"title: {item.title}\nuri: {item.uri}\n{item.snippet}",
            )
            for item in sources
        ]
        return (
            f"{question}\nResearch angle: {plan.research_angle}\n\n"
            "Sources:\n" + "\n\n".join(blocks)
        )

    def _parse(self, content: str) -> SynthesisOutput:
        """Extract and validate the synthesiser's structured output."""
        try:
            obj = json.loads(extract_json_object(content))
            return parse_typed(_SYNTHESIS_BOUNDARY, obj, SynthesisOutput)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            logger.warning(
                RESEARCH_LLM_OUTPUT_INVALID,
                stage="synthesis",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Synthesiser returned unparseable output"
            raise ResearchSynthesisError(msg) from exc
