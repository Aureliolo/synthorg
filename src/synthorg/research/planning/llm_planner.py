"""LLM-backed query planner.

Decomposes a brief into source-targeted sub-queries via a single
deterministic completion. The brief's title and question are untrusted
agent/operator input, so they are wrapped before reaching the prompt.
"""

import json
from typing import Final

from pydantic import ValidationError

from synthorg.core.boundary import parse_typed
from synthorg.engine.prompt_safety import (
    TAG_TASK_DATA,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.research import RESEARCH_LLM_OUTPUT_INVALID
from synthorg.providers.protocol import CompletionProvider
from synthorg.research._args import PlannerOutput
from synthorg.research._llm import complete_text, extract_json_object
from synthorg.research.errors import ResearchRunError
from synthorg.research.models import ResearchBrief, ResearchQueryPlan, SubQuery

logger = get_logger(__name__)

_PLAN_BOUNDARY: Final[str] = "research.plan"

_SYSTEM_PROMPT: Final[str] = (
    "You are a research planner. Decompose the research brief into focused "
    "sub-queries, each targeting exactly one allowed retrieval source. Return "
    "ONLY a JSON object with this shape:\n"
    '{"research_angle": "<lens for synthesis>", "sub_queries": '
    '[{"source_type": "<one of the allowed sources>", '
    '"query_text": "<search query>", "intent": "<why this query helps>"}]}\n'
    "Use only the allowed source types listed in the request. Prefer diverse, "
    "complementary queries over near-duplicates. "
    + untrusted_content_directive((TAG_TASK_DATA,))
)


class LlmQueryPlanner:
    """Produces a query plan with a single deterministic LLM call."""

    __slots__ = ("_model", "_provider")

    def __init__(
        self,
        *,
        provider: CompletionProvider,
        model: str,
    ) -> None:
        self._provider = provider
        self._model = model

    async def plan(self, brief: ResearchBrief) -> tuple[ResearchQueryPlan, float]:
        """Return a query plan and the USD cost of producing it."""
        allowed = tuple(st.value for st in brief.enabled_source_types)
        payload = wrap_untrusted(
            TAG_TASK_DATA,
            f"Title: {brief.title}\nQuestion: {brief.question}",
        )
        user = (
            f"Allowed source types: {', '.join(allowed)}.\n"
            f"Emit at most {brief.max_subqueries} sub-queries.\n\n"
            f"{payload}"
        )
        content, cost = await complete_text(
            self._provider,
            self._model,
            system=_SYSTEM_PROMPT,
            user=user,
        )
        output = self._parse(content)
        sub_queries = self._build_sub_queries(brief, output)
        plan = ResearchQueryPlan(
            brief_id=brief.brief_id,
            research_angle=output.research_angle,
            sub_queries=sub_queries,
        )
        return plan, cost

    def _parse(self, content: str) -> PlannerOutput:
        """Extract and validate the planner's structured output.

        Returns:
            The parsed, validated ``PlannerOutput``.

        Raises:
            ResearchRunError: When the model output is not parseable into
                a valid ``PlannerOutput``.
        """
        try:
            obj = json.loads(extract_json_object(content))
            return parse_typed(_PLAN_BOUNDARY, obj, PlannerOutput)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            logger.warning(
                RESEARCH_LLM_OUTPUT_INVALID,
                stage="planning",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Query planner returned unparseable output"
            raise ResearchRunError(msg) from exc

    @staticmethod
    def _build_sub_queries(
        brief: ResearchBrief,
        output: PlannerOutput,
    ) -> tuple[SubQuery, ...]:
        """Filter to enabled sources, cap to the budget, and index in order.

        Falls back to one query per enabled source (using the brief
        question) when the planner emits nothing usable, so the pipeline
        always has at least one sub-query.

        Returns:
            The enabled, budget-capped sub-queries, re-indexed in order;
            never empty.
        """
        enabled = set(brief.enabled_source_types)
        kept: list[SubQuery] = []
        for planned in output.sub_queries:
            if planned.source_type not in enabled:
                continue
            if len(kept) >= brief.max_subqueries:
                break
            kept.append(
                SubQuery(
                    index=len(kept),
                    source_type=planned.source_type,
                    query_text=planned.query_text,
                    intent=planned.intent,
                )
            )
        if kept:
            return tuple(kept)
        return tuple(
            SubQuery(
                index=index,
                source_type=source_type,
                query_text=brief.question,
                intent="Fallback: direct query of the brief question.",
            )
            for index, source_type in enumerate(brief.enabled_source_types)
        )
