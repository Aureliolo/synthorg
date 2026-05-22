"""Domain models for the deep CEO interview to project charter flow.

A vague one-line product idea is turned, through a structured
requirements-elicitation interview, into a single :class:`ProjectCharter`
artifact the user reviews, edits, and approves. On approval the charter
becomes the authoritative input that drives a real project run through
the work pipeline spine.

The interview reuses the Chief of Staff conversation substrate
(``Conversation`` + ``ConversationTurn``); these models cover only the
charter artifact, the structured interview decision, and the service
and controller boundary args.
"""

from typing import Literal, Self
from uuid import UUID  # noqa: TC003 -- required at runtime by Pydantic

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from synthorg.budget.currency import CurrencyCode  # noqa: TC001
from synthorg.core.enums import CharterStatus
from synthorg.core.types import NotBlankStr  # noqa: TC001

# ── Charter content building blocks ───────────────────────────────


class BudgetEnvelope(BaseModel):
    """The budget and time envelope elicited during the interview.

    Attributes:
        amount: Total budget ceiling for the project run, in
            ``currency``. Stamped as the run hard ceiling on approval.
        currency: ISO 4217 code; must match the live ``budget.currency``
            setting at approval time (enforced by the dispatcher).
        deadline: Optional hard deadline for the project.
        time_horizon: Optional free-text horizon (e.g. "2 weeks") when
            an absolute deadline is not yet known.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    amount: float = Field(gt=0.0, description="Budget ceiling in `currency`")
    currency: CurrencyCode = Field(description="ISO 4217 currency code")
    deadline: AwareDatetime | None = Field(default=None)
    time_horizon: NotBlankStr | None = Field(default=None)


class ScopeBoundaries(BaseModel):
    """Explicit in-scope and out-of-scope statements for the project.

    Attributes:
        in_scope: Capabilities/outcomes the project commits to deliver.
        out_of_scope: Capabilities/outcomes deliberately excluded.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    in_scope: tuple[NotBlankStr, ...] = ()
    out_of_scope: tuple[NotBlankStr, ...] = ()


def _validate_project_binding(
    project_id: NotBlankStr | None,
    proposed_project_name: NotBlankStr | None,
) -> None:
    """Enforce the existing-vs-new project XOR.

    A charter either references an existing project (``project_id``) or
    proposes a brand-new one (``proposed_project_name``), never both and
    never neither.

    Raises:
        ValueError: When zero or both project bindings are set.
    """
    existing = project_id is not None
    proposed = proposed_project_name is not None
    if existing == proposed:
        msg = (
            "exactly one of project_id or proposed_project_name must be set"
            f" (project_id={project_id!r}, "
            f"proposed_project_name={proposed_project_name!r})"
        )
        raise ValueError(msg)


# ── Interview output (one structured model turn) ──────────────────


class CharterDraft(BaseModel):
    """The structured charter the interview strategy emits.

    Carries content + project binding + envelope only; lifecycle and
    dispatch provenance are added by the service when it mints the
    persisted :class:`ProjectCharter`.

    Attributes:
        title: Short human-readable charter title.
        brief: The elaborated goal statement / project brief.
        goals: Concrete goals the project pursues.
        constraints: Constraints the work must respect.
        success_criteria: Measurable criteria for project success;
            become the task acceptance criteria on approval.
        scope: Explicit in/out scope boundaries.
        envelope: Budget and time envelope.
        project_id: Existing project to file the run under (XOR).
        proposed_project_name: Name of a new project to create (XOR).
        proposed_project_description: Description for the new project.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    title: NotBlankStr
    brief: NotBlankStr
    goals: tuple[NotBlankStr, ...] = ()
    constraints: tuple[NotBlankStr, ...] = ()
    success_criteria: tuple[NotBlankStr, ...] = ()
    scope: ScopeBoundaries = Field(default_factory=ScopeBoundaries)
    envelope: BudgetEnvelope
    project_id: NotBlankStr | None = None
    proposed_project_name: NotBlankStr | None = None
    proposed_project_description: str = ""

    @model_validator(mode="after")
    def _validate_binding(self) -> Self:
        """Enforce the existing-vs-new project XOR."""
        _validate_project_binding(self.project_id, self.proposed_project_name)
        return self


class InterviewDecision(BaseModel):
    """Structured output of one interview model turn.

    Exactly one branch is taken: either the interviewer asks a single
    elicitation question, or it emits a complete charter draft. The
    strategy self-asserts coverage (goals, constraints, success
    criteria, scope, envelope all populated) by emitting ``draft``
    instead of ``next_question``.

    Attributes:
        needs_more: ``True`` while requirements are still being elicited.
        next_question: The question to put to the user; required iff
            ``needs_more``.
        draft: The completed charter draft; set iff not ``needs_more``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    needs_more: bool
    next_question: NotBlankStr | None = None
    draft: CharterDraft | None = None

    @model_validator(mode="after")
    def _validate_exclusive_branch(self) -> Self:
        """Enforce the elicit-XOR-draft invariant."""
        if self.needs_more:
            if self.next_question is None:
                msg = "next_question is required when needs_more is True"
                raise ValueError(msg)
            if self.draft is not None:
                msg = "draft must be None when needs_more is True"
                raise ValueError(msg)
        else:
            if self.draft is None:
                msg = "draft is required when needs_more is False"
                raise ValueError(msg)
            if self.next_question is not None:
                msg = "next_question must be None when needs_more is False"
                raise ValueError(msg)
        return self


# ── The persisted charter artifact ────────────────────────────────


class ProjectCharter(BaseModel):
    """The reviewable, approvable project charter artifact.

    Persisted via ``CharterRepository``. Created in ``DRAFTED`` when the
    interview converges, edited in place during review, and transitioned
    to ``APPROVED`` (dispatched to the spine) or ``CANCELLED``.

    Attributes:
        id: Unique charter identifier.
        conversation_id: Originating interview conversation id.
        created_by: User id that ran the interview.
        version: Monotonic edit version (starts at 1).
        status: Lifecycle state.
        title, brief, goals, constraints, success_criteria, scope,
            envelope: Charter content (see :class:`CharterDraft`).
        project_id / proposed_project_name / proposed_project_description:
            Project binding (existing-vs-new XOR).
        created_at, updated_at: Row timestamps.
        approved_at, approved_by: Set iff ``status`` is ``APPROVED``.
        forecast_id, correlation_id, task_id: Dispatch provenance set on
            approval; ``None`` otherwise.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr
    conversation_id: NotBlankStr
    created_by: NotBlankStr
    version: int = Field(default=1, ge=1)
    status: CharterStatus = CharterStatus.DRAFTED

    title: NotBlankStr
    brief: NotBlankStr
    goals: tuple[NotBlankStr, ...] = ()
    constraints: tuple[NotBlankStr, ...] = ()
    success_criteria: tuple[NotBlankStr, ...] = ()
    scope: ScopeBoundaries = Field(default_factory=ScopeBoundaries)
    envelope: BudgetEnvelope

    project_id: NotBlankStr | None = None
    proposed_project_name: NotBlankStr | None = None
    proposed_project_description: str = ""

    created_at: AwareDatetime
    updated_at: AwareDatetime
    approved_at: AwareDatetime | None = None
    approved_by: NotBlankStr | None = None
    forecast_id: UUID | None = None
    correlation_id: NotBlankStr | None = None
    task_id: NotBlankStr | None = None

    @model_validator(mode="after")
    def _validate_binding(self) -> Self:
        """Enforce the existing-vs-new project XOR."""
        _validate_project_binding(self.project_id, self.proposed_project_name)
        return self

    @model_validator(mode="after")
    def _validate_approval_coupling(self) -> Self:
        """Approval provenance is populated iff the charter is APPROVED."""
        approved = self.status is CharterStatus.APPROVED
        provenance = (
            self.approved_at,
            self.approved_by,
            self.forecast_id,
            self.correlation_id,
            self.task_id,
        )
        any_set = any(value is not None for value in provenance)
        all_set = all(value is not None for value in provenance)
        if approved and not all_set:
            msg = "an APPROVED charter must carry full dispatch provenance"
            raise ValueError(msg)
        if not approved and any_set:
            msg = f"a {self.status.value} charter must not carry approval provenance"
            raise ValueError(msg)
        return self


# ── Service + controller boundary args ────────────────────────────


class InterviewTurnArgs(BaseModel):
    """Args for one :meth:`CharterInterviewService.run_turn` turn.

    Attributes:
        message: The user's natural-language message this turn.
        created_by: User id that owns the conversation.
        conversation_id: Existing conversation to continue, or ``None``
            to open a new interview.
        project: Optional existing project id the run should target;
            used as a hint when the interview drafts the charter.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    message: NotBlankStr
    created_by: NotBlankStr
    conversation_id: NotBlankStr | None = None
    project: NotBlankStr | None = None


class CharterEditArgs(BaseModel):
    """Args for an in-place charter edit during review.

    Every field is optional; only provided fields are updated
    (replace semantics, ``None`` skips). ``status`` is never editable
    here -- approval and cancellation have dedicated transitions.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    title: NotBlankStr | None = None
    brief: NotBlankStr | None = None
    goals: tuple[NotBlankStr, ...] | None = None
    constraints: tuple[NotBlankStr, ...] | None = None
    success_criteria: tuple[NotBlankStr, ...] | None = None
    scope: ScopeBoundaries | None = None
    envelope: BudgetEnvelope | None = None


class InterviewTurnResult(BaseModel):
    """Outcome of one interview turn.

    Exactly one branch: an elicitation question (conversation stays
    open) or a drafted charter (conversation moves to PROPOSED).

    Attributes:
        conversation_id: The conversation this turn belongs to.
        status: ``"needs_more"`` or ``"drafted"``.
        next_question: Set iff ``status == "needs_more"``.
        charter: The drafted charter; set iff ``status == "drafted"``.
        conversation_closed: ``True`` when the interview turn cap was
            reached and the conversation was force-closed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    conversation_id: NotBlankStr
    status: Literal["needs_more", "drafted"]
    next_question: NotBlankStr | None = None
    charter: ProjectCharter | None = None
    conversation_closed: bool = False

    @model_validator(mode="after")
    def _validate_status_payload(self) -> Self:
        """Enforce branch invariants between ``status`` and payload."""
        if self.status == "needs_more":
            if self.next_question is None:
                msg = "next_question is required when status is 'needs_more'"
                raise ValueError(msg)
            if self.charter is not None:
                msg = "charter must be None when status is 'needs_more'"
                raise ValueError(msg)
        else:
            if self.charter is None:
                msg = "charter is required when status is 'drafted'"
                raise ValueError(msg)
            if self.next_question is not None:
                msg = "next_question must be None when status is 'drafted'"
                raise ValueError(msg)
        return self


class CharterApprovalResult(BaseModel):
    """Outcome of approving a charter and dispatching the project run.

    Attributes:
        charter: The approved charter (with dispatch provenance stamped).
        project_id: The project the run was filed under.
        task_id: The spine-created task id.
        is_success: Whether the pipeline run reported success.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    charter: ProjectCharter
    project_id: NotBlankStr
    task_id: NotBlankStr
    is_success: bool


__all__ = [
    "BudgetEnvelope",
    "CharterApprovalResult",
    "CharterDraft",
    "CharterEditArgs",
    "InterviewDecision",
    "InterviewTurnArgs",
    "InterviewTurnResult",
    "ProjectCharter",
    "ScopeBoundaries",
]
