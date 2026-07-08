"""Agent-callable tool to put a project-shaping decision to a human.

Lets a lead agent surface a decision it cannot make on its own (a
framework choice, a packaging option, an asset direction) and wait for a
human to choose. Creates an ``ApprovalItem`` (source ``PARKED_CONTEXT`` so
the existing mid-execution resume path restores the run) marked both as a
clarification (so the task moves to ``AWAITING_INPUT`` while it waits) and
as a decision (so the human's answer is recorded as a project-brain
DECISION entry on resume). The chosen option rides back in as the decision
reason that ``ApprovalGate.build_resume_message`` injects.
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


class RequestProjectDecisionArgs(BaseModel):
    """Args for ``request_project_decision``."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    question: NotBlankStr = Field(
        max_length=4096,
        description="The decision to put to the human (what must be chosen)",
    )
    options: tuple[NotBlankStr, ...] = Field(
        default=(),
        max_length=_MAX_OPTIONS,
        description="The options to choose between (may be empty for open-ended)",
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
                "to a human (a framework choice, a packaging option, an asset "
                "direction). Provide the question and, when the choice is "
                "between known options, list them. Execution pauses until the "
                "human decides; their choice is recorded and given back to you."
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
            arguments: Must contain ``question``; ``options`` is optional.

        Returns:
            ``ToolExecutionResult`` with ``requires_parking=True``,
            ``clarification=True`` and ``decision=True`` in metadata on
            success, or an error result on failure.
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
        options = tuple(opt.strip() for opt in args.options)
        approval_id = str(uuid4())

        store_error = await self._persist_item(approval_id, question, options)
        if store_error is not None:
            return store_error

        return self._build_success(approval_id, question, options)

    async def _persist_item(
        self,
        approval_id: str,
        question: str,
        options: tuple[str, ...],
    ) -> ToolExecutionResult | None:
        """Create and persist the decision approval item.

        Returns ``None`` on success, or an error result on failure.

        Returns:
            The resulting ``ToolExecutionResult``, or ``None`` when unavailable.
        """
        try:
            from synthorg.approval.enums import ApprovalSource  # noqa: PLC0415
            from synthorg.core.approval import ApprovalItem  # noqa: PLC0415

            item = ApprovalItem(
                id=UUID(approval_id),
                action_type=_DECISION_ACTION_TYPE,
                title="Project decision requested",
                description=question,
                requested_by=self._agent_id,
                risk_level=ApprovalRiskLevel.LOW,
                # Reuse the parked-context source so the existing
                # mid-execution resume path restores the run; the decision
                # marker below drives the project-brain DECISION record.
                source=ApprovalSource.PARKED_CONTEXT,
                created_at=datetime.now(UTC),
                task_id=self._task_id,
                metadata={
                    "source": "request_project_decision",
                    "clarification": "true",
                    "decision": "true",
                    "options": json.dumps(list(options)),
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
        options: tuple[str, ...],
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
        options_hint = f" Options: {', '.join(options)}." if options else ""
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
