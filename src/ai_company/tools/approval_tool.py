"""Agent-callable tool to request human approval.

Allows agents to explicitly request human approval for sensitive
actions.  Creates an ``ApprovalItem`` in the approval store and
returns metadata signalling that the execution should be parked
until the approval decision arrives.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from ai_company.core.enums import ApprovalRiskLevel, ToolCategory
from ai_company.core.validation import is_valid_action_type
from ai_company.observability import get_logger
from ai_company.observability.events.approval_gate import (
    APPROVAL_GATE_ESCALATION_DETECTED,
    APPROVAL_GATE_ESCALATION_FAILED,
    APPROVAL_GATE_RISK_CLASSIFIED,
    APPROVAL_GATE_RISK_CLASSIFY_FAILED,
)

from .base import BaseTool, ToolExecutionResult

if TYPE_CHECKING:
    from ai_company.api.approval_store import ApprovalStore
    from ai_company.security.timeout.risk_tier_classifier import (
        DefaultRiskTierClassifier,
    )

logger = get_logger(__name__)


class RequestHumanApprovalTool(BaseTool):
    """Agent-callable tool to request human approval for a sensitive action.

    When executed, creates an ``ApprovalItem`` in the approval store
    and returns a ``ToolExecutionResult`` with metadata indicating
    that the agent should be parked until the approval decision arrives.

    Args:
        approval_store: Store to persist approval items.
        risk_classifier: Optional classifier to assess risk level.
            When ``None``, defaults to ``ApprovalRiskLevel.HIGH``.
        agent_id: Agent requesting approval.
        task_id: Optional associated task identifier.
    """

    def __init__(
        self,
        *,
        approval_store: ApprovalStore,
        risk_classifier: DefaultRiskTierClassifier | None = None,
        agent_id: str,
        task_id: str | None = None,
    ) -> None:
        super().__init__(
            name="request_human_approval",
            description=(
                "Request human approval for a sensitive action. "
                "Use this when you need explicit human authorization "
                "before proceeding with a high-risk operation. "
                "Provide the action_type (category:action format), "
                "a short title, and a detailed description."
            ),
            category=ToolCategory.OTHER,
            action_type="comms:internal",
            parameters_schema={
                "type": "object",
                "properties": {
                    "action_type": {
                        "type": "string",
                        "maxLength": 128,
                        "description": (
                            "Action type in category:action format "
                            "(e.g. 'deploy:production', 'db:admin')"
                        ),
                    },
                    "title": {
                        "type": "string",
                        "maxLength": 256,
                        "description": "Short summary of the approval request",
                    },
                    "description": {
                        "type": "string",
                        "maxLength": 4096,
                        "description": "Detailed explanation of what needs approval",
                    },
                },
                "required": ["action_type", "title", "description"],
                "additionalProperties": False,
            },
        )
        self._approval_store = approval_store
        self._risk_classifier = risk_classifier
        self._agent_id = agent_id
        self._task_id = task_id

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Create an approval item and signal parking.

        Args:
            arguments: Must contain ``action_type``, ``title``, and
                ``description``.

        Returns:
            ``ToolExecutionResult`` with ``requires_parking=True`` in
            metadata on success, or an error result on failure.
        """
        try:
            action_type = arguments["action_type"]
            title = arguments["title"]
            description = arguments["description"]
        except KeyError as exc:
            return ToolExecutionResult(
                content=(
                    f"Missing required argument: {exc}. "
                    f"Required: action_type, title, description"
                ),
                is_error=True,
            )

        if (
            not isinstance(action_type, str)
            or not isinstance(title, str)
            or not isinstance(description, str)
            or not action_type.strip()
            or not title.strip()
            or not description.strip()
        ):
            return ToolExecutionResult(
                content=(
                    "Arguments action_type, title, and description "
                    "must be non-empty strings"
                ),
                is_error=True,
            )

        action_type = action_type.strip()
        title = title.strip()
        description = description.strip()

        validation_error = self._validate_action_type(action_type)
        if validation_error is not None:
            return validation_error

        risk_level = self._classify_risk(action_type)
        approval_id = f"approval-{uuid4().hex}"

        store_error = await self._persist_item(
            approval_id,
            action_type,
            title,
            description,
            risk_level,
        )
        if store_error is not None:
            return store_error

        return self._build_success(approval_id, action_type, risk_level, title)

    async def _persist_item(
        self,
        approval_id: str,
        action_type: str,
        title: str,
        description: str,
        risk_level: ApprovalRiskLevel,
    ) -> ToolExecutionResult | None:
        """Create and persist the approval item.

        Returns ``None`` on success, or an error result on failure.
        """
        try:
            from ai_company.core.approval import ApprovalItem  # noqa: PLC0415

            item = ApprovalItem(
                id=approval_id,
                action_type=action_type,
                title=title,
                description=description,
                requested_by=self._agent_id,
                risk_level=risk_level,
                created_at=datetime.now(UTC),
                task_id=self._task_id,
                metadata={"source": "request_human_approval"},
            )
            await self._approval_store.add(item)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.exception(
                APPROVAL_GATE_ESCALATION_FAILED,
                agent_id=self._agent_id,
                action_type=action_type,
                error=str(exc),
                note="Failed to create approval item",
            )
            return ToolExecutionResult(
                content="Failed to create approval request",
                is_error=True,
            )
        return None

    def _build_success(
        self,
        approval_id: str,
        action_type: str,
        risk_level: ApprovalRiskLevel,
        title: str,
    ) -> ToolExecutionResult:
        """Build the success result with parking metadata."""
        logger.info(
            APPROVAL_GATE_ESCALATION_DETECTED,
            approval_id=approval_id,
            agent_id=self._agent_id,
            action_type=action_type,
            risk_level=risk_level.value,
            title=title,
        )
        return ToolExecutionResult(
            content=(
                f"Approval request created (id={approval_id}). "
                f"Execution will be paused until a human approves or "
                f"rejects this request. Action: {title}"
            ),
            is_error=False,
            metadata={
                "requires_parking": True,
                "approval_id": approval_id,
                "action_type": action_type,
                "risk_level": risk_level.value,
            },
        )

    @staticmethod
    def _validate_action_type(action_type: str) -> ToolExecutionResult | None:
        """Validate action_type has ``category:action`` format.

        Returns ``None`` if valid, or an error result if invalid.
        """
        if not is_valid_action_type(action_type):
            return ToolExecutionResult(
                content=(
                    f"Invalid action_type {action_type!r}: "
                    f"must use 'category:action' format "
                    f"(e.g. 'deploy:production')"
                ),
                is_error=True,
            )
        return None

    def _classify_risk(self, action_type: str) -> ApprovalRiskLevel:
        """Classify the risk level of the action.

        Falls back to HIGH when no classifier is configured or when
        classification fails.
        """
        if self._risk_classifier is not None:
            try:
                level = self._risk_classifier.classify(action_type)
            except MemoryError, RecursionError:
                raise
            except Exception:
                logger.exception(
                    APPROVAL_GATE_RISK_CLASSIFY_FAILED,
                    action_type=action_type,
                    note="Risk classification failed — defaulting to HIGH",
                )
                return ApprovalRiskLevel.HIGH
            logger.debug(
                APPROVAL_GATE_RISK_CLASSIFIED,
                action_type=action_type,
                risk_level=level.value,
            )
            return level
        return ApprovalRiskLevel.HIGH
