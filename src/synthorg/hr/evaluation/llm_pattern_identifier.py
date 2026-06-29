"""Provider-backed pattern identifier.

Identifies cross-agent weakness patterns with a dedicated LLM call over the
per-agent pillar scores, going beyond the deterministic threshold count: it
can weigh several pillars together and surface a systemic weakness a fixed
per-pillar cut-off would miss. It degrades to an injected deterministic
:class:`PatternIdentifier` on any provider or parsing failure, so a cycle
never stalls on an unavailable or misbehaving model.

Only numeric pillar scores are sent to the model. Agents are keyed by a
positional index (``agent_0``, ``agent_1``, ...), never by their id, so no
operator-set free-form string crosses the prompt boundary (the model output
is pillar-keyed and never references an agent, so agent identity is not
needed).
"""

import json
from typing import ClassVar, Final

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.json_parsing import extract_json_from_llm_response
from synthorg.core.types import NotBlankStr
from synthorg.hr.evaluation.enums import EvaluationPillar
from synthorg.hr.evaluation.models import EvaluationReport
from synthorg.hr.evaluation.pattern_protocols import PatternIdentifier
from synthorg.llm.metadata import ModelPinMetadata
from synthorg.llm.model_pins import pin_for
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.eval_loop import (
    EVAL_LOOP_LLM_FALLBACK,
    EVAL_LOOP_PATTERN_IDENTIFIED,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.errors import ProviderError
from synthorg.providers.models import ChatMessage, CompletionConfig
from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)

_VALID_PILLARS: Final[frozenset[str]] = frozenset(p.value for p in EvaluationPillar)

_SYSTEM_PROMPT: Final[str] = (
    "You are an evaluation analyst for a fleet of AI agents. Given each "
    "agent's five-pillar scores (0.0-1.0; lower is weaker), identify the "
    "pillars on which the fleet is systemically weak. Weigh consistency "
    "across agents, not just a single low score. Reply ONLY with JSON: "
    '{"patterns": ["weakness:<pillar>", ...]} using these pillar names: '
    f"{sorted(_VALID_PILLARS)}. Return an empty list if none are weak."
)

_TASK_ID: NotBlankStr = NotBlankStr("system:hr:eval_pattern_analysis")


class LlmPatternIdentifier:
    """Identifies weakness patterns via a dedicated LLM call."""

    _PURPOSE_ID: ClassVar[PromptPurposeId] = PromptPurposeId.HR_EVAL_PATTERN_ANALYSIS

    __slots__ = ("_cost_tracker", "_fallback", "_model", "_provider")

    def __init__(
        self,
        provider: CompletionProvider,
        *,
        model: NotBlankStr,
        fallback: PatternIdentifier,
        cost_tracker: CostTrackerProtocol | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._fallback = fallback
        self._cost_tracker = cost_tracker

    @property
    def metadata(self) -> ModelPinMetadata:
        """Pinned model + sampling for this prompt class."""
        return pin_for(self._PURPOSE_ID)

    async def identify(
        self,
        reports: tuple[EvaluationReport, ...],
    ) -> tuple[NotBlankStr, ...]:
        """Identify weakness patterns, falling back to the deterministic strategy.

        Returns:
            Patterns in the form ``"weakness:<pillar>"``.
        """
        if not reports:
            return ()
        content = await self._call_model(reports)
        if content is None:
            return await self._fallback.identify(reports)
        if not content.strip():
            logger.warning(
                EVAL_LOOP_LLM_FALLBACK, step="identify", reason="empty_response"
            )
            return await self._fallback.identify(reports)
        patterns = _parse_patterns(content)
        if patterns is None:
            logger.warning(
                EVAL_LOOP_LLM_FALLBACK, step="identify", reason="unparseable"
            )
            return await self._fallback.identify(reports)
        if patterns:
            logger.info(
                EVAL_LOOP_PATTERN_IDENTIFIED,
                pattern_count=len(patterns),
                patterns=list(patterns),
                source="llm",
            )
        return patterns

    async def _call_model(
        self,
        reports: tuple[EvaluationReport, ...],
    ) -> str | None:
        """Call the provider, returning content or ``None`` to trigger fallback.

        Returns:
            The model response content, or ``None`` on a recoverable failure.

        Raises:
            ProviderError: Re-raised on a non-retryable provider failure.
        """
        payload = json.dumps(_score_rows(reports), sort_keys=True)
        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content=_SYSTEM_PROMPT),
            ChatMessage(role=MessageRole.USER, content=payload),
        ]
        config = CompletionConfig(
            temperature=self.metadata.temperature,
            max_tokens=self.metadata.max_tokens,
        )
        try:
            async with cost_recording_scope(
                cost_tracker=self._cost_tracker,
                agent_id=_TASK_ID,
                task_id=_TASK_ID,
                purpose=self.metadata.prompt_class_id,
                call_category=LLMCallCategory.SYSTEM,
            ):
                response = await self._provider.complete(
                    messages, self._model, config=config
                )
        except ProviderError as exc:
            if not exc.is_retryable:
                raise
            logger.warning(
                EVAL_LOOP_LLM_FALLBACK,
                step="identify",
                reason="provider_error_retryable",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            # Reached only by non-ProviderError exceptions: a code defect or
            # unexpected infra fault, not a provider condition. ERROR + a
            # distinct reason so it is not mistaken for a provider outage.
            logger.error(
                EVAL_LOOP_LLM_FALLBACK,
                step="identify",
                reason="unexpected_internal_error",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None
        return response.content


def _score_rows(
    reports: tuple[EvaluationReport, ...],
) -> dict[str, dict[str, float]]:
    """Build positional ``agent_N -> {pillar: score}`` numeric rows.

    Agents are keyed by index, never by ``agent_id``, so no operator-set
    free-form string enters the prompt (the model output is pillar-keyed and
    never references an agent).

    Returns:
        The score rows.
    """
    return {
        f"agent_{index}": {
            score.pillar.value: round(score.score, 4) for score in report.pillar_scores
        }
        for index, report in enumerate(reports)
    }


def _parse_patterns(content: str | None) -> tuple[NotBlankStr, ...] | None:
    """Parse weakness tokens from the model response.

    Returns:
        Validated, de-duplicated weakness tokens, or ``None`` when the
        response could not be parsed at all (caller falls back).
    """
    if not content or not content.strip():
        return None
    data = extract_json_from_llm_response(content)
    if data is None:
        return None
    patterns_raw = data.get("patterns")
    if not isinstance(patterns_raw, list):
        return None
    seen: set[str] = set()
    out: list[NotBlankStr] = []
    for raw in patterns_raw:
        if not isinstance(raw, str) or ":" not in raw:
            continue
        kind, pillar = raw.split(":", 1)
        token = f"weakness:{pillar}"
        if kind == "weakness" and pillar in _VALID_PILLARS and token not in seen:
            seen.add(token)
            out.append(NotBlankStr(token))
    return tuple(out)
