"""Pluggable interview strategy for the deep CEO interview.

The orchestrator (``CharterInterviewService``) owns the conversation
plumbing; the strategy owns one structured model turn. The default
``LLMCharterInterviewer`` calls a completion provider and parses the
strict ``InterviewDecision`` JSON contract.
"""

from typing import Protocol, runtime_checkable

from pydantic import ValidationError

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.tracker import CostTracker
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.json_parsing import extract_json_from_llm_response
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.meta.charter.config import CharterConfig
from synthorg.meta.charter.models import InterviewDecision
from synthorg.meta.charter.prompts import (
    CHARTER_INTERVIEW_SYSTEM,
    CHARTER_INTERVIEW_USER,
)
from synthorg.meta.chief_of_staff.models import ConversationTurn
from synthorg.meta.errors import CharterInterviewResponseInvalidError
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.charter import (
    CHARTER_INTERVIEW_FAILED,
    CHARTER_INTERVIEW_RESPONSE_INVALID,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig
from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)

_NO_PROJECT_HINT: str = "No existing project was supplied; propose a new project."


def _render_history(turns: tuple[ConversationTurn, ...]) -> str:
    """Render chronological turns into a prompt-ready transcript.

    Returns:
        Resulting string.
    """
    return "\n".join(f"{turn.role.value.upper()}: {turn.content}" for turn in turns)


def _render_project_hint(project_id: str | None) -> str:
    """Describe the project binding the interview should target.

    Returns:
        Resulting string.
    """
    if project_id is None:
        return _NO_PROJECT_HINT
    return (
        f"An existing project '{project_id}' was supplied; set the charter's"
        " project_id to it and leave proposed_project_name null."
    )


@runtime_checkable
class CharterInterviewStrategy(Protocol):
    """One structured interview turn: elicit a question or draft a charter."""

    async def run_turn(
        self,
        history: tuple[ConversationTurn, ...],
        *,
        project_id: NotBlankStr | None,
        currency: str,
    ) -> InterviewDecision:
        """Run one interview turn over *history*.

        Args:
            history: Chronological conversation turns (oldest first),
                including the latest user message.
            project_id: An existing project to target, or ``None`` to
                let the interview propose a new project.
            currency: ISO 4217 code the charter envelope must use.

        Returns:
            The structured elicit-or-draft decision.

        Raises:
            CharterInterviewResponseInvalidError: When the model output
                violates the structured contract.
        """
        ...


class LLMCharterInterviewer:
    """LLM-backed :class:`CharterInterviewStrategy`.

    Args:
        provider: LLM completion provider.
        config: Charter-interview configuration.
        cost_tracker: Optional cost tracker for LLM accounting.
    """

    def __init__(
        self,
        *,
        provider: CompletionProvider,
        config: CharterConfig,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        self._provider = provider
        self._config = config
        self._cost_tracker = cost_tracker

    async def run_turn(
        self,
        history: tuple[ConversationTurn, ...],
        *,
        project_id: NotBlankStr | None,
        currency: str,
    ) -> InterviewDecision:
        """Call the model and parse its structured interview output.

        Returns:
            ``InterviewDecision`` instance.

        Raises:
            Exception: Provider call failed.
            CharterInterviewResponseInvalidError: Provider response
                failed validation.
        """
        system = CHARTER_INTERVIEW_SYSTEM.format(
            project_hint=wrap_untrusted(
                TAG_TASK_DATA, _render_project_hint(project_id)
            ),
            currency=wrap_untrusted(TAG_TASK_DATA, currency),
        )
        user = CHARTER_INTERVIEW_USER.format(
            conversation_history=wrap_untrusted(
                TAG_TASK_DATA, _render_history(history)
            ),
        )
        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content=system),
            ChatMessage(role=MessageRole.USER, content=user),
        ]
        completion_config = CompletionConfig(
            temperature=self._config.interview_temperature,
            max_tokens=self._config.interview_max_tokens,
        )
        try:
            async with cost_recording_scope(
                cost_tracker=self._cost_tracker,
                agent_id=NotBlankStr("system"),
                task_id=NotBlankStr("system:charter:interview"),
                call_category=LLMCallCategory.SYSTEM,
            ):
                response = await self._provider.complete(
                    messages,
                    self._config.interview_model,
                    config=completion_config,
                )
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(logger, CHARTER_INTERVIEW_FAILED, exc)
            raise
        raw = (response.content or "").strip()
        parsed = extract_json_from_llm_response(
            raw,
            logger_callback=lambda detail: logger.warning(
                CHARTER_INTERVIEW_RESPONSE_INVALID, detail=detail
            ),
        )
        if parsed is None:
            logger.warning(
                CHARTER_INTERVIEW_RESPONSE_INVALID,
                reason="llm_response_not_parseable",
                error_type=CharterInterviewResponseInvalidError.__name__,
            )
            raise CharterInterviewResponseInvalidError
        try:
            return InterviewDecision.model_validate(parsed)
        except ValidationError as exc:
            logger.warning(
                CHARTER_INTERVIEW_RESPONSE_INVALID,
                detail="schema_validation_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise CharterInterviewResponseInvalidError from exc


__all__ = ["CharterInterviewStrategy", "LLMCharterInterviewer"]
