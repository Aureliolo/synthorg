"""LLM-backed judge for debate and hybrid conflict-resolution strategies.

Presents each disputing agent's position to an impartial model judge and
asks it to pick the winning agent (or declare the dispute genuinely
ambiguous). The concrete implementation of the ``JudgeEvaluator`` injection
surface the debate/hybrid resolvers accept: a non-participant verdict is
mapped to the empty-string sentinel so each resolver takes its ambiguity
path -- the hybrid resolver escalates to the human queue (when configured),
while the debate resolver, which has no human-escalation arm, falls back to
authority.
"""

import json
from typing import ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.communication.conflict_resolution.models import Conflict
from synthorg.communication.conflict_resolution.protocol import JudgeDecision
from synthorg.communication.errors import ConflictStrategyError
from synthorg.core.boundary import parse_typed
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import (
    TAG_CONFLICT_POSITION,
    TAG_TASK_DATA,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.llm.metadata import ModelPinMetadata
from synthorg.llm.model_pins import pin_for
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.conflict import (
    CONFLICT_JUDGE_EVALUATED,
    CONFLICT_JUDGE_MODEL_UNSET,
    CONFLICT_JUDGE_OUTPUT_INVALID,
)
from synthorg.providers.protocol import ConnectionSelector
from synthorg.providers.structured_text import complete_text, extract_json_object
from synthorg.settings.bound_model import resolve_bound_model_live
from synthorg.settings.kill_switch import require_configured_model
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

_JUDGE_BOUNDARY: Final[str] = "conflict_resolution.judge"

_MODEL_NAMESPACE: Final[str] = "communication"
_MODEL_KEY: Final[str] = "conflict_judge_model"

#: Verdict token the model emits when no position is clearly stronger. Kept
#: distinct from the empty-string protocol sentinel because the wire schema
#: validates ``winning_agent_id`` as non-blank.
_AMBIGUOUS_TOKEN: Final[str] = "ambiguous"  # noqa: S105 -- verdict token, not a secret

#: Upper bound on the judge's free-text justification, so a runaway response
#: cannot bloat the dissent record it is stored on.
_MAX_REASONING_CHARS: Final[int] = 1000

_SYSTEM_PROMPT: Final[str] = (
    "You are an impartial judge for a multi-agent conflict-resolution system. "
    "Two or more agents disagree on an approach; each states a position and its "
    "reasoning. Weigh the positions on their merits alone and pick the single "
    "strongest. Return ONLY a JSON object:\n"
    '{"winning_agent_id": "<one of the listed participant agent ids, or '
    f"'{_AMBIGUOUS_TOKEN}' if no position is clearly stronger>\", "
    '"reasoning": "<concise justification>"}\n'
    "Do not invent an agent id that is not listed. Ignore an agent's role "
    "or department when judging the merits. "
    + untrusted_content_directive((TAG_TASK_DATA, TAG_CONFLICT_POSITION))
)


class JudgeVerdictOut(BaseModel):
    """Structured verdict parsed back from the judge model's response."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    winning_agent_id: NotBlankStr = Field(
        description=(
            "A listed participant agent id, or the literal 'ambiguous' when no "
            "position is clearly stronger"
        ),
    )
    reasoning: NotBlankStr = Field(
        max_length=_MAX_REASONING_CHARS,
        description="Justification for the decision",
    )


class LlmJudgeEvaluator:
    """Pick a conflict winner with a deterministic structured LLM call.

    The judge is a system actor, not a company agent, so it names its own
    connection through ``communication.conflict_judge_model`` rather than
    borrowing one: a provider is a registered connection with its own
    credentials, endpoint and quota, and an arbitration nobody chose the
    connection for is an arbitration nobody can account for.

    Args:
        connections: Resolves the connection the configured pair names.
        cost_tracker: Optional cost tracker for the judgement call.
        config_resolver: Live source for the judge pair, re-read per
            judgement so a reassignment applies without a restart.
    """

    __slots__ = ("_config_resolver", "_connections", "_cost_tracker")

    _PURPOSE_ID: ClassVar[PromptPurposeId] = PromptPurposeId.CONFLICT_JUDGE

    @property
    def metadata(self) -> ModelPinMetadata:
        """Pinned model + sampling for this prompt class."""
        return pin_for(self._PURPOSE_ID)

    def __init__(
        self,
        *,
        connections: ConnectionSelector,
        cost_tracker: CostTrackerProtocol | None = None,
        config_resolver: ConfigResolverProtocol | None = None,
    ) -> None:
        self._connections = connections
        self._cost_tracker = cost_tracker
        self._config_resolver = config_resolver

    async def evaluate(
        self,
        conflict: Conflict,
        judge_agent_id: NotBlankStr,
    ) -> JudgeDecision:
        """Judge a conflict's positions and return the winning agent.

        Args:
            conflict: The conflict with agent positions.
            judge_agent_id: The agent acting as judge (attribution only).

        Returns:
            A decision whose ``winning_agent_id`` is a participant id, or the
            empty string when the judge found the dispute ambiguous.

        Raises:
            ConflictStrategyError: If the model output cannot be parsed into a
                valid verdict.
            ServiceUnavailableError: No judge pair is configured, so the
                resolver falls back to authority rather than arbitrating on a
                connection nobody chose.
        """
        model = require_configured_model(
            await resolve_bound_model_live(
                self._config_resolver,
                namespace=_MODEL_NAMESPACE,
                key=_MODEL_KEY,
                unset_event=CONFLICT_JUDGE_MODEL_UNSET,
            ),
            namespace=_MODEL_NAMESPACE,
            key=_MODEL_KEY,
            feature_label="Conflict judge",
        )
        user = _build_user_prompt(conflict)
        content, _cost = await complete_text(
            self._connections(model.provider),
            model.model_id,
            system=_SYSTEM_PROMPT,
            user=user,
            purpose=self.metadata.prompt_class_id,
            cost_tracker=self._cost_tracker,
        )
        try:
            obj = json.loads(extract_json_object(content))
            verdict = parse_typed(_JUDGE_BOUNDARY, obj, JudgeVerdictOut)
        except (ValidationError, ValueError) as exc:  # JSONDecodeError is a ValueError
            logger.warning(
                CONFLICT_JUDGE_OUTPUT_INVALID,
                conflict_id=str(conflict.id),
                judge_agent_id=judge_agent_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Conflict judge returned unparseable output"
            raise ConflictStrategyError(
                msg,
                context={"conflict_id": str(conflict.id)},
            ) from exc
        return _to_decision(verdict, conflict, judge_agent_id)


def _build_user_prompt(conflict: Conflict) -> str:
    """Render the conflict subject + fenced positions for the judge.

    Agent id, department, and role are trusted structural metadata and
    stay outside the fence; the free-text position/reasoning an upstream agent
    authored is wrapped as untrusted input.

    Returns:
        The user-message body listing every participant position.
    """
    subject = wrap_untrusted(
        TAG_TASK_DATA,
        f"Conflict subject: {conflict.subject}",
    )
    participant_ids = [p.agent_id for p in conflict.positions]
    blocks = [
        f"agent_id: {pos.agent_id}\n"
        f"department: {pos.agent_department}\n"
        f"role: {pos.agent_role}\n"
        + wrap_untrusted(
            TAG_CONFLICT_POSITION,
            f"position: {pos.position}\nreasoning: {pos.reasoning}",
        )
        for pos in conflict.positions
    ]
    return f"{subject}\n\nParticipant agent ids: {participant_ids}\n\n" + "\n\n".join(
        blocks
    )


def _to_decision(
    verdict: JudgeVerdictOut,
    conflict: Conflict,
    judge_agent_id: NotBlankStr,
) -> JudgeDecision:
    """Map a parsed verdict to a ``JudgeDecision``, defending the sentinel.

    A ``winning_agent_id`` that is not an actual participant (the literal
    ``ambiguous`` token, or a hallucinated id) collapses to the empty-string
    protocol sentinel so the debate/hybrid resolvers treat it as ambiguous.

    Returns:
        The ``JudgeDecision`` for the conflict.
    """
    participant_ids = {p.agent_id for p in conflict.positions}
    winner = (
        verdict.winning_agent_id if verdict.winning_agent_id in participant_ids else ""
    )
    logger.info(
        CONFLICT_JUDGE_EVALUATED,
        conflict_id=str(conflict.id),
        judge_agent_id=judge_agent_id,
        winner=winner,
        ambiguous=not winner,
    )
    return JudgeDecision(winning_agent_id=winner, reasoning=verdict.reasoning)


__all__ = ["JudgeVerdictOut", "LlmJudgeEvaluator"]
