"""Agent-callable tool to pause and ask a human a clarifying question.

Lets an executing agent stop mid-task, surface a question, and wait for
a human answer instead of guessing. Creates an ``ApprovalItem`` in the
approval store (source ``PARKED_CONTEXT`` so the existing mid-execution
resume path restores the run) and returns metadata signalling that the
loop should park. Unlike ``request_human_approval`` the park is marked
``clarification`` so the task moves to ``AWAITING_INPUT`` while it waits,
and the human's free-text answer rides back in as the decision reason
that ``ApprovalGate.build_resume_message`` injects on resume.
"""

from datetime import UTC, datetime
from typing import ClassVar, override
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from synthorg.approval.enums import ApprovalRiskLevel, QuestionReversibility
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.boundary import parse_typed
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_ESCALATION_DETECTED,
    APPROVAL_GATE_ESCALATION_FAILED,
)
from synthorg.security.autonomy.enums import ToolCategory

from .base import BaseTool, ToolExecutionResult

logger = get_logger(__name__)

#: Action type for a clarification park. Structurally a
#: ``category:action`` pair (the invoker/park path expects that shape);
#: not a security-classified action -- clarification carries no risk.
_CLARIFY_ACTION_TYPE: str = "clarify:question"


class RequestClarificationArgs(BaseModel):
    """Args for ``request_clarification``."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    question: NotBlankStr = Field(
        max_length=4096,
        description="The clarifying question to put to the human",
    )
    reversibility: QuestionReversibility = Field(
        description=(
            "Whether the choice behind this question is 'reversible' (a quick "
            "edit undoes it) or 'hard_to_reverse' (undoing it costs real "
            "rework). Required: judging it is part of deciding to ask."
        ),
    )


class RequestClarificationTool(BaseTool):
    """Agent-callable tool to pause and ask a human a clarifying question.

    When executed, creates an ``ApprovalItem`` in the approval store and
    returns a ``ToolExecutionResult`` whose metadata signals parking with
    ``clarification=True``, so the loop parks the agent and the task moves
    to ``AWAITING_INPUT`` until the human answers.

    Args:
        approval_store: Store to persist the clarification item.
        agent_id: Agent asking the question.
        task_id: Optional associated task identifier.
    """

    args_model: ClassVar[type[BaseModel] | None] = RequestClarificationArgs

    def __init__(
        self,
        *,
        approval_store: ApprovalStoreProtocol,
        agent_id: str,
        task_id: str | None = None,
    ) -> None:
        super().__init__(
            name="request_clarification",
            description=(
                "Pause and ask a human a clarifying question when you "
                "genuinely cannot proceed without their input (an "
                "ambiguous requirement, a missing decision). Execution "
                "pauses until the human answers; their answer is then "
                "given back to you so you can continue. Use sparingly -- "
                "prefer making a reasonable assumption and stating it."
            ),
            category=ToolCategory.OTHER,
            action_type="comms:internal",
            parameters_schema=RequestClarificationArgs.model_json_schema(),
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
        """Create a clarification item and signal parking.

        Args:
            arguments: Must contain ``question``.

        Returns:
            ``ToolExecutionResult`` with ``requires_parking=True`` and
            ``clarification=True`` in metadata on success, or an error
            result on failure.
        """
        try:
            args = parse_typed(
                "tool.request_clarification", arguments, RequestClarificationArgs
            )
        except ValidationError as exc:
            # Report the field that actually failed (blank, too long, or an
            # unexpected extra) rather than a fixed message. Uses loc + msg
            # only, never the raw input value.
            details = "; ".join(
                f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}"
                for err in exc.errors()
            )
            return ToolExecutionResult(
                content=f"Invalid clarification arguments: {details}",
                is_error=True,
            )

        question = args.question.strip()
        approval_id = str(uuid4())

        store_error = await self._persist_item(
            approval_id, question, args.reversibility
        )
        if store_error is not None:
            return store_error

        return self._build_success(approval_id, question, args.reversibility)

    async def _persist_item(
        self,
        approval_id: str,
        question: str,
        reversibility: QuestionReversibility,
    ) -> ToolExecutionResult | None:
        """Create and persist the clarification approval item.

        Returns ``None`` on success, or an error result on failure.

        Returns:
            The resulting ``ToolExecutionResult``, or ``None`` when unavailable.
        """
        try:
            from synthorg.approval.enums import ApprovalSource  # noqa: PLC0415
            from synthorg.core.approval import ApprovalItem  # noqa: PLC0415

            item = ApprovalItem(
                id=UUID(approval_id),
                action_type=_CLARIFY_ACTION_TYPE,
                title="Clarification requested",
                description=question,
                requested_by=self._agent_id,
                risk_level=ApprovalRiskLevel.LOW,
                # Reuse the parked-context source so the existing
                # mid-execution resume path restores the run and injects
                # the human's answer; the clarification marker below is
                # what distinguishes the task-status behaviour.
                source=ApprovalSource.PARKED_CONTEXT,
                created_at=datetime.now(UTC),
                task_id=self._task_id,
                metadata={
                    "source": "request_clarification",
                    "clarification": "true",
                    "reversibility": reversibility.value,
                },
            )
            await self._approval_store.add(item)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                APPROVAL_GATE_ESCALATION_FAILED,
                agent_id=self._agent_id,
                action_type=_CLARIFY_ACTION_TYPE,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="Failed to create clarification item",
            )
            return ToolExecutionResult(
                content="Failed to create clarification request",
                is_error=True,
            )
        return None

    def _build_success(
        self,
        approval_id: str,
        question: str,
        reversibility: QuestionReversibility,
    ) -> ToolExecutionResult:
        """Build the success result with parking + clarification metadata.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        logger.info(
            APPROVAL_GATE_ESCALATION_DETECTED,
            approval_id=approval_id,
            agent_id=self._agent_id,
            action_type=_CLARIFY_ACTION_TYPE,
            risk_level=ApprovalRiskLevel.LOW.value,
            title="Clarification requested",
        )
        return ToolExecutionResult(
            content=(
                f"Clarification requested (id={approval_id}). Execution "
                f"will pause until a human answers: {question}"
            ),
            is_error=False,
            metadata={
                "requires_parking": True,
                "clarification": True,
                "approval_id": approval_id,
                "action_type": _CLARIFY_ACTION_TYPE,
                "risk_level": ApprovalRiskLevel.LOW.value,
                "reversibility": reversibility.value,
            },
        )
