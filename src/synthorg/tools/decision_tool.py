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
continues with, injected by ``ApprovalGate.build_resume_message``.
"""

import json
from datetime import UTC, datetime
from typing import ClassVar, override
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.boundary import parse_typed
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.evidence import EvidencePackage, RecommendedAction
from synthorg.core.plan import PlanOption
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_ESCALATION_DETECTED,
    APPROVAL_GATE_ESCALATION_FAILED,
)
from synthorg.security.autonomy.enums import ToolCategory

from .base import BaseTool, ToolExecutionResult

logger = get_logger(__name__)

#: Action type for a project-decision park. Structurally a
#: ``category:action`` pair; the actually-emitted successor to the dead
#: ``arch:decide`` action type.
_DECISION_ACTION_TYPE: str = "decision:project"

#: Bound on the number of options a single decision may present, so a
#: runaway model cannot flood the human with choices.
_MAX_OPTIONS: int = 12

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
        default=(),
        max_length=_MAX_OPTIONS,
        description=(
            "The options to choose between, each with a title, a writeup of "
            "its tradeoffs, and whether you recommend it (>=2 when given, "
            "exactly one recommended, unique ids). Empty for an open-ended "
            "decision the human answers in free text."
        ),
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
                "engine, an asset direction). When the choice is between known "
                "options, list each with a title, a writeup of its tradeoffs, "
                "and whether you recommend it (mark exactly one). Execution "
                "pauses until the human picks; their choice is recorded and "
                "given back to you. Omit options for an open-ended decision "
                "the human answers in free text."
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
            arguments: Must contain ``question``; ``options`` is optional (and
                when given carries the rich per-option writeups).

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
            # Surface which field failed (question, options, or an unexpected
            # extra) rather than a fixed "question" message that misreports an
            # options / extra-field error. Uses loc + msg only, never the raw
            # input value.
            details = "; ".join(
                f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}"
                for err in exc.errors()
            )
            return ToolExecutionResult(
                content=f"Invalid decision arguments: {details}",
                is_error=True,
            )

        question = args.question.strip()
        approval_id = str(uuid4())
        try:
            evidence = self._build_evidence(approval_id, args)
        except ValueError as exc:
            # The options violated the decision invariants (fewer than two /
            # not exactly one recommended / duplicate ids). Report the
            # invariant message, never the raw input beyond it.
            return ToolExecutionResult(
                content=f"Invalid decision options: {safe_error_description(exc)}",
                is_error=True,
            )

        store_error = await self._persist_item(approval_id, args, evidence)
        if store_error is not None:
            return store_error

        return self._build_success(approval_id, question, args.options)

    def _build_evidence(
        self, approval_id: str, args: RequestProjectDecisionArgs
    ) -> EvidencePackage | None:
        """Build the decision evidence package, or ``None`` for open-ended.

        Returns:
            An :class:`EvidencePackage` carrying the rich options, or ``None``
            when the decision is open-ended (no options).

        Raises:
            ValueError: When the options violate the decision invariants.
        """
        if not args.options:
            return None
        options = tuple(
            PlanOption(
                id=opt.id,
                title=opt.title,
                summary=opt.summary,
                recommended=opt.recommended,
            )
            for opt in args.options
        )
        return EvidencePackage(
            id=NotBlankStr(approval_id),
            title=args.question,
            narrative=args.question,
            recommended_actions=(_PROCEED_ACTION,),
            options=options,
            source_agent_id=NotBlankStr(self._agent_id),
            task_id=NotBlankStr(self._task_id) if self._task_id else None,
            risk_level=ApprovalRiskLevel.LOW,
            created_at=datetime.now(UTC),
        )

    async def _persist_item(
        self,
        approval_id: str,
        args: RequestProjectDecisionArgs,
        evidence: EvidencePackage | None,
    ) -> ToolExecutionResult | None:
        """Create and persist the decision approval item.

        Returns ``None`` on success, or an error result on failure.

        Returns:
            The resulting ``ToolExecutionResult``, or ``None`` when unavailable.
        """
        try:
            from synthorg.approval.enums import ApprovalSource  # noqa: PLC0415
            from synthorg.core.approval import ApprovalItem  # noqa: PLC0415

            # The brain-DECISION record reads the alternatives from this
            # titles list; the rich per-option writeups live on the evidence
            # package the operator picks from.
            option_titles = [opt.title for opt in args.options]
            item = ApprovalItem(
                id=UUID(approval_id),
                action_type=_DECISION_ACTION_TYPE,
                title="Project decision requested",
                description=args.question,
                requested_by=self._agent_id,
                risk_level=ApprovalRiskLevel.LOW,
                # Reuse the parked-context source so the existing
                # mid-execution resume path restores the run; the decision
                # marker below drives the project-brain DECISION record.
                source=ApprovalSource.PARKED_CONTEXT,
                created_at=datetime.now(UTC),
                task_id=self._task_id,
                evidence_package=evidence,
                metadata={
                    "source": "request_project_decision",
                    "clarification": "true",
                    "decision": "true",
                    "options": json.dumps(option_titles),
                },
            )
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
            },
        )
