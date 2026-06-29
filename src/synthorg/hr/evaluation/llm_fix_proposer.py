"""Provider-backed fix proposer.

Proposes remediation action identifiers for identified weakness patterns
with a dedicated LLM call, going beyond the static per-pillar table: it can
account for the combination of weaknesses in a cycle rather than mapping
each pillar in isolation. It degrades to an injected deterministic
:class:`FixProposer` on any provider or parsing failure, so a cycle always
yields actionable output.

Only the weakness pattern tokens (``"weakness:<pillar>"``) are sent to the
model, so no free-form agent content crosses the prompt boundary.
"""

import json
import re
from typing import ClassVar, Final

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.collections import dedupe_preserving_order
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.json_parsing import extract_json_from_llm_response
from synthorg.core.types import NotBlankStr
from synthorg.hr.evaluation.pattern_protocols import FixProposer
from synthorg.llm.metadata import ModelPinMetadata
from synthorg.llm.model_pins import pin_for
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.eval_loop import (
    EVAL_LOOP_ACTION_PROPOSED,
    EVAL_LOOP_LLM_FALLBACK,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.errors import ProviderError
from synthorg.providers.models import ChatMessage, CompletionConfig
from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)

_SYSTEM_PROMPT: Final[str] = (
    "You propose remediation actions for an AI agent fleet. Given weakness "
    "patterns (each 'weakness:<pillar>'), propose concise snake_case action "
    "identifiers a remediation system can act on (e.g. 'increase_review_depth', "
    "'add_recovery_training'). Reply ONLY with JSON: "
    '{"actions": ["<action_id>", ...]}. Return an empty list if no action is '
    "warranted."
)

_TASK_ID: NotBlankStr = NotBlankStr("system:hr:eval_fix_proposal")

#: Shape a model-returned action id must match (the snake_case form the
#: system prompt asks for). Anything else is dropped before it can reach a
#: notification sink, closing a log / alert injection surface.
_ACTION_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"[a-z][a-z0-9_]{0,63}")


class LlmFixProposer:
    """Proposes remediation action ids via a dedicated LLM call."""

    _PURPOSE_ID: ClassVar[PromptPurposeId] = PromptPurposeId.HR_EVAL_FIX_PROPOSAL

    __slots__ = ("_cost_tracker", "_fallback", "_model", "_provider")

    def __init__(
        self,
        provider: CompletionProvider,
        *,
        model: NotBlankStr,
        fallback: FixProposer,
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

    async def propose(
        self,
        patterns: tuple[NotBlankStr, ...],
    ) -> tuple[NotBlankStr, ...]:
        """Propose action ids, falling back to the deterministic strategy.

        Returns:
            Ordered, de-duplicated action identifiers.
        """
        if not patterns:
            return ()
        content = await self._call_model(patterns)
        if content is None:
            return await self._fallback.propose(patterns)
        if not content.strip():
            logger.warning(
                EVAL_LOOP_LLM_FALLBACK, step="propose", reason="empty_response"
            )
            return await self._fallback.propose(patterns)
        actions = _parse_actions(content)
        if actions is None:
            logger.warning(EVAL_LOOP_LLM_FALLBACK, step="propose", reason="unparseable")
            return await self._fallback.propose(patterns)
        if actions:
            logger.info(
                EVAL_LOOP_ACTION_PROPOSED,
                action_count=len(actions),
                actions=list(actions),
                source="llm",
            )
        return actions

    async def _call_model(
        self,
        patterns: tuple[NotBlankStr, ...],
    ) -> str | None:
        """Call the provider, returning content or ``None`` to trigger fallback.

        Returns:
            The model response content, or ``None`` on a recoverable failure.

        Raises:
            ProviderError: Re-raised on a non-retryable provider failure.
        """
        payload = json.dumps({"patterns": list(patterns)}, sort_keys=True)
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
                step="propose",
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
                step="propose",
                reason="unexpected_internal_error",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None
        return response.content


def _parse_actions(content: str | None) -> tuple[NotBlankStr, ...] | None:
    """Parse action identifiers from the model response.

    Each id must match the snake_case shape the system prompt asks for; a
    model-returned value carrying newlines, markup, or other unexpected
    content is dropped rather than interpolated into the operator alert.

    Returns:
        De-duplicated, validated action ids, or ``None`` when the response
        could not be parsed at all (caller falls back).
    """
    if not content or not content.strip():
        return None
    data = extract_json_from_llm_response(content)
    if data is None:
        return None
    actions_raw = data.get("actions")
    if not isinstance(actions_raw, list):
        return None
    actions = [
        NotBlankStr(raw.strip())
        for raw in actions_raw
        if isinstance(raw, str) and _ACTION_ID_PATTERN.fullmatch(raw.strip())
    ]
    return tuple(dedupe_preserving_order(actions))
