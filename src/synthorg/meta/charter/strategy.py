"""Pluggable interview strategy for the deep CEO interview.

The orchestrator (``CharterInterviewService``) owns the conversation
plumbing; the strategy owns one structured model turn. The default
``LLMCharterInterviewer`` calls a completion provider and parses the
strict ``InterviewDecision`` JSON contract.
"""

from typing import ClassVar, Final, Protocol, runtime_checkable

from pydantic import ValidationError

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.json_parsing import extract_json_from_llm_response
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.llm.metadata import ModelPinMetadata
from synthorg.llm.model_pins import pin_for
from synthorg.llm.prompt_purpose import PromptPurposeId
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
from synthorg.providers.protocol import ConnectionSelector
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.kill_switch import require_configured_model
from synthorg.settings.model_ref import ModelRef

logger = get_logger(__name__)

_NO_PROJECT_HINT: str = "No existing project was supplied; propose a new project."

#: One first ask and one repair. A model that cannot produce the envelope
#: twice, given its own refused output, is not going to on a third try, and
#: the operator is waiting on a chat turn.
_INTERVIEW_ATTEMPTS: Final[int] = 2

#: What the repair turn tells the model. Its own output goes back verbatim
#: because the shape of the mistake is the thing to correct, and the reason
#: is the validator's, so a caller never has to guess which field was wrong.
_REPAIR_INSTRUCTION: Final[str] = (
    "Your previous reply did not match the required structure and was "
    "rejected:\n{refusal}\n\nHere is what you sent:\n{raw}\n\nSend the same "
    "content again as a single JSON object matching the schema exactly. Every "
    "required field must be present at the top level, and no field outside "
    "the schema may appear. Nest the charter fields inside the charter object "
    "rather than at the top level."
)


def _decide(raw: str, *, attempt: int) -> tuple[InterviewDecision | None, str]:
    """Parse one interview response.

    Args:
        raw: The model's response text.
        attempt: Which attempt produced it, for the log.

    Returns:
        The decision and an empty reason, or ``None`` and the reason it was
        refused, phrased for the repair turn.
    """
    parsed = extract_json_from_llm_response(
        raw,
        logger_callback=lambda detail: logger.warning(
            CHARTER_INTERVIEW_RESPONSE_INVALID, detail=detail, attempt=attempt
        ),
    )
    if parsed is None:
        logger.warning(
            CHARTER_INTERVIEW_RESPONSE_INVALID,
            reason="llm_response_not_parseable",
            attempt=attempt,
            error_type=CharterInterviewResponseInvalidError.__name__,
        )
        return None, "The reply was not valid JSON."
    try:
        return InterviewDecision.model_validate(parsed), ""
    except ValidationError as exc:
        logger.warning(
            CHARTER_INTERVIEW_RESPONSE_INVALID,
            detail="schema_validation_failed",
            attempt=attempt,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None, safe_error_description(exc)


def _repair_turn(raw: str, refusal: str) -> list[ChatMessage]:
    """Build the turn that asks the model to correct its own reply.

    The model's output is untrusted content on the way back in, exactly as
    it was on the way out, so it is fenced like any other.

    Args:
        raw: The refused response.
        refusal: Why it was refused.

    Returns:
        The one user message carrying both.
    """
    return [
        ChatMessage(
            role=MessageRole.USER,
            content=_REPAIR_INSTRUCTION.format(
                refusal=wrap_untrusted(TAG_TASK_DATA, refusal),
                raw=wrap_untrusted(TAG_TASK_DATA, raw),
            ),
        )
    ]


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
        config: CharterConfig,
    ) -> InterviewDecision:
        """Run one interview turn over *history*.

        Args:
            history: Chronological conversation turns (oldest first),
                including the latest user message.
            project_id: An existing project to target, or ``None`` to
                let the interview propose a new project.
            config: The interview config resolved live for this turn
                (model, sampling, and the envelope currency).

        Returns:
            The structured elicit-or-draft decision.

        Raises:
            CharterInterviewResponseInvalidError: When the model output
                violates the structured contract.
        """
        ...


class LLMCharterInterviewer:
    """LLM-backed :class:`CharterInterviewStrategy`.

    The per-turn model, sampling, and currency are read from the
    ``CharterConfig`` passed to :meth:`run_turn` (resolved live by the
    service), so a ``/settings`` change to a ``charter.*`` knob lands on
    the next interview turn without a restart.

    Args:
        connections: Resolves the connection the configured
            ``charter.interview_model`` pair names.
        cost_tracker: Optional cost tracker for LLM accounting.
    """

    _PURPOSE_ID: ClassVar[PromptPurposeId] = PromptPurposeId.CHARTER_INTERVIEW

    @property
    def metadata(self) -> ModelPinMetadata:
        """Pinned model + sampling for this prompt class."""
        return pin_for(self._PURPOSE_ID)

    def __init__(
        self,
        *,
        connections: ConnectionSelector,
        cost_tracker: CostTrackerProtocol | None = None,
    ) -> None:
        self._connections = connections
        self._cost_tracker = cost_tracker

    async def run_turn(
        self,
        history: tuple[ConversationTurn, ...],
        *,
        project_id: NotBlankStr | None,
        config: CharterConfig,
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
            currency=wrap_untrusted(TAG_TASK_DATA, config.default_currency),
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
            temperature=config.interview_temperature,
            max_tokens=config.interview_max_tokens,
        )
        model = require_configured_model(
            config.interview_model,
            namespace=SettingNamespace.CHARTER,
            key="interview_model",
            feature_label="Charter interview",
        )
        # Re-asked once on a malformed answer, with the model's own output and
        # the reason it was refused. This is the ONE intake path the product
        # has, so a single badly-shaped structured response would otherwise
        # end the conversation: a live interview died on turn three when the
        # model returned the budget object where the decision envelope goes,
        # and the operator was shown an exception class name.
        attempt_messages = messages
        for attempt in range(_INTERVIEW_ATTEMPTS):
            raw = await self._complete(attempt_messages, model, completion_config)
            decision, refusal = _decide(raw, attempt=attempt)
            if decision is not None:
                return decision
            attempt_messages = [*messages, *_repair_turn(raw, refusal)]
        raise CharterInterviewResponseInvalidError

    async def _complete(
        self,
        messages: list[ChatMessage],
        model: ModelRef,
        completion_config: CompletionConfig,
    ) -> str:
        """Run one interview completion.

        Args:
            messages: The rendered prompt.
            model: The bound provider / model pair.
            completion_config: Sampling settings for the call.

        Returns:
            The response text, stripped.

        Raises:
            Exception: Whatever the provider raised, after logging.
        """
        try:
            async with cost_recording_scope(
                cost_tracker=self._cost_tracker,
                purpose=self.metadata.prompt_class_id,
                call_category=LLMCallCategory.SYSTEM,
            ):
                response = await self._connections(model.provider).complete(
                    messages,
                    model.model_id,
                    config=completion_config,
                )
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(logger, CHARTER_INTERVIEW_FAILED, exc)
            raise
        return (response.content or "").strip()


__all__ = ["CharterInterviewStrategy", "LLMCharterInterviewer"]
