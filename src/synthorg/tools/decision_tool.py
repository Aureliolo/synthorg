"""Agent-callable tool to put a project-shaping decision to a human.

Lets a lead agent surface a decision it cannot make on its own (a
framework choice, an architecture for the core engine, an asset
direction) and wait for a human to choose. When the choice is between
known options, the agent supplies each with a title, a writeup of its
tradeoffs, and whether it recommends it; those ride on an
:class:`EvidencePackage` so the operator picks structurally (by option
id) rather than typing free text. Creates an ``ApprovalItem`` (source
``PARKED_CONTEXT`` so the existing mid-execution resume path restores the
run) marked both as a clarification (so the task moves to
``AWAITING_INPUT`` while it waits) and as a decision (so the human's
choice is recorded as a project-brain DECISION entry on resume). The
chosen option's writeup rides back in as the decision the parked agent
continues with, injected by ``build_resume_message``.
"""

import json
from datetime import UTC, datetime
from typing import ClassVar, Final, Self, override
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from synthorg.approval.enums import ApprovalRiskLevel, QuestionReversibility
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.approval.questions import DECISION_ACTION_TYPE
from synthorg.core.boundary import parse_typed
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.evidence import EvidencePackage, RecommendedAction
from synthorg.core.plan import PlanOption, validate_decision_options
from synthorg.core.plan_enums import PlanItemKind
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_ESCALATION_DETECTED,
    APPROVAL_GATE_ESCALATION_FAILED,
)
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools._question_output_guard import guard_question_text

from .base import BaseTool, ToolExecutionResult

logger = get_logger(__name__)

#: Action type for a project-decision park. Structurally a
#: ``category:action`` pair; the actually-emitted successor to the dead
#: ``arch:decide`` action type.
_DECISION_ACTION_TYPE: Final[str] = DECISION_ACTION_TYPE

#: Every project decision must offer at least two structured options so the
#: operator always picks by option id rather than approving with no decision.
_MIN_OPTIONS: Final[int] = 2

#: Bound on the number of options a single decision may present, so a
#: runaway model cannot flood the human with choices.
_MAX_OPTIONS: Final[int] = 12

#: Cap for the evidence package's compact ``title`` label, derived from the
#: (up to 4096-char) question so the title stays a short summary distinct from
#: the full ``narrative``.
_TITLE_MAX_LEN: Final[int] = 120


def _short_title(question: str) -> NotBlankStr:
    """Derive a compact label from the (possibly long) decision question.

    Returns:
        The whitespace-collapsed question, truncated to a short label.
    """
    compact = " ".join(question.split())
    if len(compact) <= _TITLE_MAX_LEN:
        return NotBlankStr(compact)
    return NotBlankStr(compact[: _TITLE_MAX_LEN - 3].rstrip() + "...")


#: The lone recommended action on an options decision: approve = proceed
#: with the option the operator picks.
_PROCEED_ACTION: RecommendedAction = RecommendedAction(
    action_type=NotBlankStr("approve"),
    label=NotBlankStr("Approve with the selected option"),
    description=NotBlankStr("Proceed with the option you pick."),
)


class DecisionOption(BaseModel):
    """One option offered for a project decision, with its tradeoffs."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(max_length=64, description="Stable option identifier")
    title: NotBlankStr = Field(max_length=200, description="Short option title")
    summary: NotBlankStr = Field(
        max_length=4096,
        description="The option's tradeoffs and rationale, so the operator can choose",
    )
    recommended: bool = Field(
        default=False,
        description="Whether you recommend this option (exactly one must be true)",
    )


class RequestProjectDecisionArgs(BaseModel):
    """Args for ``request_project_decision``."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    question: NotBlankStr = Field(
        max_length=4096,
        description="The decision to put to the human (what must be chosen)",
    )
    options: tuple[DecisionOption, ...] = Field(
        min_length=_MIN_OPTIONS,
        max_length=_MAX_OPTIONS,
        description=(
            "The options to choose between, each with a title, a writeup of "
            "its tradeoffs, and whether you recommend it (at least two, "
            "exactly one recommended, unique ids)."
        ),
    )
    reversibility: QuestionReversibility = Field(
        description=(
            "Whether the choice behind this decision is 'reversible' (a quick "
            "edit undoes it) or 'hard_to_reverse' (undoing it costs real "
            "rework). Required: judging it is part of deciding to ask."
        ),
    )

    @model_validator(mode="after")
    def _validate_options(self) -> Self:
        """Enforce the decision-option invariants at the boundary type itself.

        Reuses the shared :func:`validate_decision_options` so the args model
        rejects malformed options (fewer than two, not exactly one recommended,
        duplicate ids) at parse time, rather than deferring the check to the
        downstream :class:`EvidencePackage` construction. Every project
        decision offers structured options, so the operator always picks by
        option id rather than being able to approve with no decision at all.

        Returns:
            The validated args.
        """
        validate_decision_options(
            entity_id="request_project_decision",
            kind=PlanItemKind.DECISION,
            options=self.plan_options(),
        )
        return self

    def plan_options(self) -> tuple[PlanOption, ...]:
        """Project the options onto the shared :class:`PlanOption` shape.

        Returns:
            The options as ``PlanOption``s (always at least two).
        """
        return tuple(
            PlanOption(
                id=opt.id,
                title=opt.title,
                summary=opt.summary,
                recommended=opt.recommended,
            )
            for opt in self.options
        )


def _guarded_args(
    args: RequestProjectDecisionArgs, approved: list[str]
) -> RequestProjectDecisionArgs:
    """Rebuild the args from the output-style-approved strings.

    ``approved`` arrives in the order the caller supplied: the question, then
    every option title, then every option summary. Copied rather than
    re-validated because a text rewrite touches no invariant the model
    enforces (option count, the single recommendation, unique ids), and
    re-validation would turn a rewrite that trips ``NotBlankStr`` into an
    exception escaping a tool that must return a result.

    Returns:
        The args with every human-facing string replaced by its approved form.
    """
    count = len(args.options)
    titles = approved[1 : 1 + count]
    summaries = approved[1 + count :]
    return args.model_copy(
        update={
            "question": NotBlankStr(approved[0]),
            "options": tuple(
                option.model_copy(
                    update={
                        "title": NotBlankStr(title),
                        "summary": NotBlankStr(summary),
                    },
                )
                for option, title, summary in zip(
                    args.options, titles, summaries, strict=True
                )
            ),
        },
    )


class RequestProjectDecisionTool(BaseTool):
    """Agent-callable tool to put a project-shaping decision to a human.

    When executed, creates an ``ApprovalItem`` whose metadata signals
    parking with ``clarification=True`` (so the task moves to
    ``AWAITING_INPUT``) and ``decision=True`` (so the answer is recorded as
    a project-brain DECISION entry on resume).

    Args:
        approval_store: Store to persist the decision item.
        agent_id: Agent asking for the decision.
        task_id: Optional associated task identifier.
    """

    args_model: ClassVar[type[BaseModel] | None] = RequestProjectDecisionArgs

    def __init__(
        self,
        *,
        approval_store: ApprovalStoreProtocol,
        agent_id: str,
        task_id: str | None = None,
    ) -> None:
        super().__init__(
            name="request_project_decision",
            description=(
                "Put a project-shaping decision you cannot make on your own "
                "to a human (a framework choice, an architecture for the core "
                "engine, an asset direction). List at least two options, each "
                "with a title, a writeup of its tradeoffs, and whether you "
                "recommend it (mark exactly one). Execution pauses until the "
                "human picks; their choice is recorded and given back to you."
            ),
            category=ToolCategory.OTHER,
            action_type="comms:internal",
            parameters_schema=RequestProjectDecisionArgs.model_json_schema(),
        )
        self._approval_store = approval_store
        self._agent_id = agent_id
        self._task_id = task_id

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Create a decision item and signal parking.

        Args:
            arguments: Must contain ``question`` and ``options`` (at least two,
                each carrying the rich per-option writeups).

        Returns:
            ``ToolExecutionResult`` with ``requires_parking=True``,
            ``clarification=True`` and ``decision=True`` in metadata on
            success, or an error result on failure (invalid args, malformed
            options, or a store failure).
        """
        try:
            args = parse_typed(
                "tool.request_project_decision",
                arguments,
                RequestProjectDecisionArgs,
            )
        except ValidationError as exc:
            return self._invalid_arguments(exc)

        # The question and every option writeup are agent-authored prose a
        # human reads in the chat transcript, so they pass the same
        # output-style boundary an inter-agent message does before persisting.
        blocked, approved = guard_question_text(
            args.question.strip(),
            *(opt.title for opt in args.options),
            *(opt.summary for opt in args.options),
        )
        if blocked is not None:
            return blocked
        # Everything downstream reads the guarded copy. An AUTO_REWRITE rule
        # that fixed the question but left the option writeups raw would
        # persist, resume with and show the operator text the boundary had
        # already ruled against.
        guarded = _guarded_args(args, approved)
        approval_id = str(uuid4())
        # One timestamp for the whole decision so the evidence package and the
        # approval item share it. Options are already validated by
        # ``RequestProjectDecisionArgs``'s model validator (rejected at
        # ``parse_typed`` above), so building the evidence cannot fail here.
        now = datetime.now(UTC)
        evidence = self._build_evidence(approval_id, guarded, now)

        store_error = await self._persist_item(approval_id, guarded, evidence, now)
        if store_error is not None:
            return store_error

        return self._build_success(
            approval_id, guarded.question, guarded.options, guarded.reversibility
        )

    def _invalid_arguments(self, exc: ValidationError) -> ToolExecutionResult:
        """Report which field failed, without echoing what the agent sent.

        Names the failing field (question, options, or an unexpected extra)
        rather than a fixed "question" message that would misreport an
        options error, and uses ``loc`` + ``msg`` only, never the raw value.
        Logged as well as returned: the error result goes back to the agent,
        so an agent looping on a malformed call is otherwise invisible to the
        operator watching the run.

        Returns:
            The error result handed back to the calling agent.
        """
        details = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}"
            for err in exc.errors()
        )
        logger.warning(
            APPROVAL_GATE_ESCALATION_FAILED,
            agent_id=self._agent_id,
            action_type=_DECISION_ACTION_TYPE,
            error_type=type(exc).__name__,
            invalid_fields=[
                ".".join(str(part) for part in err["loc"]) for err in exc.errors()
            ],
            note="Malformed decision arguments",
        )
        return ToolExecutionResult(
            content=f"Invalid decision arguments: {details}",
            is_error=True,
        )

    def _build_evidence(
        self, approval_id: str, args: RequestProjectDecisionArgs, now: datetime
    ) -> EvidencePackage:
        """Build the decision evidence package carrying the rich options.

        Returns:
            An :class:`EvidencePackage` carrying the rich options.
        """
        return EvidencePackage(
            id=NotBlankStr(approval_id),
            title=_short_title(args.question),
            narrative=args.question,
            recommended_actions=(_PROCEED_ACTION,),
            options=args.plan_options(),
            source_agent_id=NotBlankStr(self._agent_id),
            task_id=NotBlankStr(self._task_id) if self._task_id else None,
            risk_level=ApprovalRiskLevel.LOW,
            created_at=now,
        )

    async def _persist_item(
        self,
        approval_id: str,
        args: RequestProjectDecisionArgs,
        evidence: EvidencePackage,
        now: datetime,
    ) -> ToolExecutionResult | None:
        """Create and persist the decision approval item.

        Returns ``None`` on success, or an error result on failure.

        Returns:
            The resulting ``ToolExecutionResult``, or ``None`` when unavailable.
        """
        from synthorg.approval.enums import ApprovalSource  # noqa: PLC0415
        from synthorg.core.approval import ApprovalItem  # noqa: PLC0415

        # The brain-DECISION record reads the alternatives from this titles
        # list; the rich per-option writeups live on the evidence package the
        # operator picks from. Constructed outside the try so a construction
        # bug surfaces distinctly from a store outage.
        option_titles = [opt.title for opt in args.options]
        item = ApprovalItem(
            id=UUID(approval_id),
            action_type=_DECISION_ACTION_TYPE,
            title="Project decision requested",
            description=args.question,
            requested_by=self._agent_id,
            risk_level=ApprovalRiskLevel.LOW,
            # Reuse the parked-context source so the existing mid-execution
            # resume path restores the run; the decision marker below drives
            # the project-brain DECISION record.
            source=ApprovalSource.PARKED_CONTEXT,
            created_at=now,
            task_id=self._task_id,
            evidence_package=evidence,
            metadata={
                "source": "request_project_decision",
                "clarification": "true",
                "decision": "true",
                "options": json.dumps(option_titles),
                "reversibility": args.reversibility.value,
            },
        )
        try:
            await self._approval_store.add(item)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                APPROVAL_GATE_ESCALATION_FAILED,
                agent_id=self._agent_id,
                action_type=_DECISION_ACTION_TYPE,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="Failed to create project-decision item",
            )
            return ToolExecutionResult(
                content="Failed to create decision request",
                is_error=True,
            )
        return None

    def _build_success(
        self,
        approval_id: str,
        question: str,
        options: tuple[DecisionOption, ...],
        reversibility: QuestionReversibility,
    ) -> ToolExecutionResult:
        """Build the success result with parking + decision metadata.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        logger.info(
            APPROVAL_GATE_ESCALATION_DETECTED,
            approval_id=approval_id,
            agent_id=self._agent_id,
            action_type=_DECISION_ACTION_TYPE,
            risk_level=ApprovalRiskLevel.LOW.value,
            title="Project decision requested",
        )
        options_hint = (
            f" Options: {', '.join(opt.title for opt in options)}." if options else ""
        )
        return ToolExecutionResult(
            content=(
                f"Decision requested (id={approval_id}). Execution will pause "
                f"until a human decides: {question}{options_hint}"
            ),
            is_error=False,
            metadata={
                "requires_parking": True,
                "clarification": True,
                "decision": True,
                "approval_id": approval_id,
                "action_type": _DECISION_ACTION_TYPE,
                "risk_level": ApprovalRiskLevel.LOW.value,
                "reversibility": reversibility.value,
            },
        )
