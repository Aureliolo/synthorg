# module-kind: service
"""Asks the LLM for the connective prose woven around the run facts.

The synthesiser is the only narrator component that calls a provider. It
formats the deterministic :class:`ReducedRun` into a fenced, untrusted
record, asks the model for an executive summary plus optional per-section
narration, and returns a :class:`NarrativeProse`. It never raises: a
provider failure, an empty reply, or malformed JSON degrades to a
deterministic fallback so the structured facts still ship.
"""

import asyncio

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.tracker import CostTracker
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.json_parsing import extract_json_from_llm_response
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.narrative.constants import FALLBACK_SUMMARY
from synthorg.meta.chief_of_staff.narrative.models import NarrativeProse, ReducedRun
from synthorg.meta.chief_of_staff.prompts import RUN_NARRATIVE_PROSE_PROMPT
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.chief_of_staff import COS_NARRATIVE_PROSE_FALLBACK
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig
from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)

_PROSE_MAX: int = 8192
_NARRATOR_TASK_ID: NotBlankStr = NotBlankStr("system:cos:narrative")
_NARRATOR_AGENT: NotBlankStr = NotBlankStr("system")


class NarrativeSynthesiser:
    """Generates the connective prose for one run narrative."""

    __slots__ = ("_config", "_cost_tracker", "_provider")

    def __init__(
        self,
        *,
        provider: CompletionProvider,
        config: ChiefOfStaffConfig,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        self._provider = provider
        self._config = config
        self._cost_tracker = cost_tracker

    async def write_prose(self, reduced: ReducedRun) -> NarrativeProse:
        """Return the connective prose for the run, or a fallback.

        Args:
            reduced: The fact-only rollup to narrate around.

        Returns:
            The model's :class:`NarrativeProse`, or a deterministic
            fallback when the call fails or returns nothing usable.
        """
        prompt = RUN_NARRATIVE_PROSE_PROMPT.format(
            brief_title=wrap_untrusted(TAG_TASK_DATA, reduced.brief_title),
            final_status=reduced.final_status.value,
            record=wrap_untrusted(TAG_TASK_DATA, _format_record(reduced)),
        )
        messages = [ChatMessage(role=MessageRole.USER, content=prompt)]
        config = CompletionConfig(
            temperature=self._config.narrative_temperature,
            max_tokens=self._config.narrative_max_tokens,
        )
        try:
            async with cost_recording_scope(
                cost_tracker=self._cost_tracker,
                agent_id=_NARRATOR_AGENT,
                task_id=_NARRATOR_TASK_ID,
                call_category=LLMCallCategory.SYSTEM,
            ):
                response = await asyncio.wait_for(
                    self._provider.complete(
                        messages,
                        self._config.narrative_model,
                        config=config,
                    ),
                    timeout=self._config.agent_call_timeout_seconds,
                )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                COS_NARRATIVE_PROSE_FALLBACK,
                reason="provider_call_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return _fallback()
        raw = (response.content or "").strip()
        parsed = extract_json_from_llm_response(
            raw,
            logger_callback=lambda detail: logger.warning(
                COS_NARRATIVE_PROSE_FALLBACK, detail=detail
            ),
        )
        if not isinstance(parsed, dict):
            return _fallback()
        summary = _clean(parsed.get("summary"))
        if summary is None:
            logger.warning(COS_NARRATIVE_PROSE_FALLBACK, reason="empty_summary")
            return _fallback()
        return NarrativeProse(
            summary=summary,
            decisions=_clean(parsed.get("decisions")),
            contributions=_clean(parsed.get("contributions")),
            outcomes=_clean(parsed.get("outcomes")),
        )


def _fallback() -> NarrativeProse:
    """Return the deterministic fallback prose.

    Returns:
        A :class:`NarrativeProse` carrying only the fixed fallback
        summary; the structured facts still render beside it.
    """
    return NarrativeProse(summary=FALLBACK_SUMMARY)


def _clean(value: object) -> str | None:
    """Normalise a model-supplied prose field to a bounded string or None.

    Returns:
        The stripped, length-bounded string, or ``None`` when the value
        is absent, not a string, or blank.
    """
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped[:_PROSE_MAX]


def _format_record(reduced: ReducedRun) -> str:
    """Render the reduced run as a plain-text record for the prompt.

    Returns:
        A bounded, human-readable summary of the run's facts.
    """
    lines: list[str] = ["Decisions:"]
    if reduced.decisions:
        for decision in reduced.decisions:
            alt = (
                f" (alternatives: {', '.join(decision.alternatives)})"
                if decision.alternatives
                else ""
            )
            lines.append(f"- {decision.outcome}{alt} -- {decision.rationale}")
    else:
        lines.append("- (none recorded)")
    lines.append("")
    lines.append("Who did what:")
    if reduced.contributions:
        for contribution in reduced.contributions:
            tools = (
                f", tools: {', '.join(contribution.tools)}"
                if contribution.tools
                else ""
            )
            lines.append(
                f"- {contribution.agent_id}: {contribution.turn_count} turn(s){tools}"
            )
    else:
        lines.append("- (no agent activity recorded)")
    lines.append("")
    lines.append("Outcomes:")
    lines.extend(f"- {line}" for line in reduced.outcomes)
    if reduced.open_items:
        lines.append("")
        lines.append("Open items:")
        lines.extend(
            f"- {item.kind}: {item.title} ({item.status})"
            for item in reduced.open_items
        )
    return "\n".join(lines)
