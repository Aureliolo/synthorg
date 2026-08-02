# module-kind: code
"""Wire models for the parked-question surface on the unified conversation.

Sibling of ``_turn_models`` for the same reason: the controller and the service
exchange these, and keeping them out of both leaves each inside its size tier.

A parked question is an ``ApprovalItem`` an agent created when it stopped to
ask; these are the projection the chat surface renders and answers.
"""

from typing import Final

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, computed_field

from synthorg.approval.enums import ApprovalStatus, QuestionReversibility
from synthorg.core.types import NotBlankStr

_QUESTION_MAX_LEN: Final[int] = 4096
_OPTION_ID_MAX_LEN: Final[int] = 64
_OPTION_TITLE_MAX_LEN: Final[int] = 200


class ParkedQuestionOption(BaseModel):
    """One structured option a project decision offers the operator."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(
        max_length=_OPTION_ID_MAX_LEN,
        description="Stable option identifier; echo it back to pick this option.",
    )
    title: NotBlankStr = Field(
        max_length=_OPTION_TITLE_MAX_LEN,
        description="Short option title.",
    )
    summary: NotBlankStr = Field(
        max_length=_QUESTION_MAX_LEN,
        description="The option's tradeoffs and rationale.",
    )
    recommended: bool = Field(
        default=False,
        description="Whether the asking agent recommends this option.",
    )


class ParkedQuestion(BaseModel):
    """An agent question waiting on a human, projected for the chat surface.

    Attributes:
        approval_id: The approval the question is recorded as; answer against it.
        question: What the agent is asking.
        asked_by_id: The asking agent's identifier.
        asked_by_name: The asking agent's display name (the id when unresolved).
        task_id: The task the agent parked, when it had one.
        task_title: That task's title, when resolvable.
        project: The project the task belongs to, when resolvable.
        reversibility: The agent's declared reversibility; ``None`` for a
            question parked before the tools required it, which renders as
            unclassified rather than inventing a value the agent never asserted.
        options: The options to pick between; empty for a clarification.
        asked_at: When the agent parked.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    approval_id: NotBlankStr
    question: NotBlankStr = Field(max_length=_QUESTION_MAX_LEN)
    asked_by_id: NotBlankStr
    asked_by_name: NotBlankStr
    task_id: NotBlankStr | None = None
    task_title: NotBlankStr | None = None
    project: NotBlankStr | None = None
    reversibility: QuestionReversibility | None = None
    options: tuple[ParkedQuestionOption, ...] = ()
    asked_at: AwareDatetime

    @computed_field
    @property
    def is_decision(self) -> bool:
        """Whether this is a structural pick rather than an open question.

        Derived rather than stored: the operator picks by option id, so what
        makes a question a decision IS having options to pick from. Carrying a
        separate flag would let the two disagree, and the disagreement that
        matters (a decision with no options) would render a pick UI with
        nothing to pick.

        Returns:
            ``True`` when the question offers options.
        """
        return bool(self.options)


class AnswerQuestionRequest(BaseModel):
    """Payload for answering a parked agent question.

    ``answer`` is required and non-blank, which is the whole reason this door
    exists: on the generic approvals endpoint the comment is optional, so an
    approve with no text resumes a clarification with no answer at all.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    answer: NotBlankStr = Field(
        max_length=_QUESTION_MAX_LEN,
        description="The answer to give the agent. Required and non-blank.",
    )
    chosen_option_id: NotBlankStr | None = Field(
        default=None,
        max_length=_OPTION_ID_MAX_LEN,
        description=(
            "For a project decision, the id of the option you pick. The chosen"
            " option's writeup becomes what the agent resumes with."
        ),
    )


class QuestionDecisionResult(BaseModel):
    """Outcome of answering or declining one parked question.

    Attributes:
        approval_id: The question that was decided.
        status: ``approved`` for an answer, ``rejected`` for a decline.
        recorded_answer: The text the agent actually resumes with, so the
            transcript echoes what was persisted rather than what was typed.
        decided_at: When the decision landed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    approval_id: NotBlankStr
    status: ApprovalStatus
    recorded_answer: NotBlankStr
    decided_at: AwareDatetime


__all__ = [
    "AnswerQuestionRequest",
    "ParkedQuestion",
    "ParkedQuestionOption",
    "QuestionDecisionResult",
]
