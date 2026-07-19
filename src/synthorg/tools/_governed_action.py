"""Reusable approval gate for connection-bound, side-effecting agent tools.

The governed external-API tool pioneered the pattern: a sensitive or
write call parks a content-addressed, one-shot approval bound to the
calling agent + task, and the re-issued identical call consumes the
grant. The forge and chat tools reuse that flow through
:class:`ConnectionApprovalGate`, keyed on an opaque
:class:`ActionSignature` (a digest over the operation + its mutation
payload) so a grant authorises exactly the call it was approved for.
"""

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC
from typing import ClassVar
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, JsonValue

from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource, ApprovalStatus
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.approval import ApprovalItem
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.effective_autonomy import EffectiveAutonomy
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.external_api import (
    EXTERNAL_API_APPROVAL_CONSUMED,
    EXTERNAL_API_APPROVAL_REQUIRED,
    EXTERNAL_API_RISK_CLASSIFY_FAILED,
    EXTERNAL_API_SIGNATURE_MISMATCH,
)
from synthorg.security.timeout.protocol import RiskTierClassifier
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.errors import ToolError

logger = get_logger(__name__)

_SIGNATURE_METADATA_KEY = "governed_action_signature"


class GovernedApprovalMismatchError(ToolError):
    """A supplied approval did not match the call, or was already consumed."""

    status_code: ClassVar[int] = 409
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_CONFLICT
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    default_message: ClassVar[str] = "Approval mismatch or already consumed"


class ActionSignature(BaseModel):
    """Immutable digest binding an approval to one specific action.

    The digest folds the operation namespace, the bound connection, and
    the mutation payload into a single hash, so approving "comment X on
    #7" never authorises "comment Y" or a call on another connection.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    digest: NotBlankStr

    @classmethod
    def build(
        cls,
        *,
        namespace: str,
        connection: str,
        operation: str,
        payload: Mapping[str, JsonValue],
    ) -> ActionSignature:
        """Construct a signature from the operation + its mutation payload.

        Returns:
            The content-addressed :class:`ActionSignature`.
        """
        canonical = json.dumps(
            {
                "ns": namespace,
                "conn": connection,
                "op": operation,
                "args": payload,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return cls(digest=NotBlankStr(digest))

    def to_metadata(self) -> dict[str, str]:
        """Serialise to an approval-metadata fragment.

        Returns:
            The single-key metadata fragment carrying the digest.
        """
        return {_SIGNATURE_METADATA_KEY: str(self.digest)}

    @classmethod
    def from_metadata(cls, metadata: Mapping[str, str]) -> ActionSignature | None:
        """Parse a signature from approval metadata, or ``None`` if absent.

        Returns:
            The parsed :class:`ActionSignature`, or ``None``.
        """
        raw = metadata.get(_SIGNATURE_METADATA_KEY)
        if not raw:
            return None
        return cls(digest=NotBlankStr(raw))

    def matches(self, other: ActionSignature | None) -> bool:
        """Whether *other* is an identical action signature.

        Returns:
            ``True`` when *other* carries the same digest.
        """
        return other is not None and self.digest == other.digest


class ConnectionApprovalGate:
    """Parks / consumes one-shot approvals for a side-effecting tool call.

    Bound to a single agent + task so one caller can never consume
    another's grant. When the effective autonomy auto-approves the action
    type, the gate is a no-op (returns ``None`` to proceed).
    """

    def __init__(  # noqa: PLR0913 -- governance collaborators are all required
        self,
        *,
        approval_store: ApprovalStoreProtocol,
        agent_id: str,
        task_id: str | None,
        action_type: str,
        effective_autonomy: EffectiveAutonomy | None = None,
        risk_classifier: RiskTierClassifier | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._approval_store = approval_store
        self._agent_id = agent_id
        self._task_id = task_id
        self._action_type = action_type
        self._risk_classifier = risk_classifier
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._auto_approved = (
            effective_autonomy is not None
            and action_type in effective_autonomy.auto_approve_actions
        )

    async def gate(
        self,
        signature: ActionSignature,
        *,
        connection: str,
        approval_id: str | None,
        title: str,
        description: str,
    ) -> ToolExecutionResult | None:
        """Consume a matching approval, or park for one.

        Returns:
            ``None`` to proceed (a matching grant was consumed or the
            action is auto-approved), or a parking ``ToolExecutionResult``
            when no approval exists yet.

        Raises:
            GovernedApprovalMismatchError: An explicitly-referenced
                approval did not match, or the consume CAS lost a race.
        """
        if self._auto_approved:
            return None
        match = await self._find_matching_approval(signature, approval_id, connection)
        if match is None:
            return await self._park(signature, connection, title, description)
        consumed = await self._approval_store.consume_if_approved(match)
        if consumed is None:
            logger.warning(
                EXTERNAL_API_SIGNATURE_MISMATCH,
                connection=connection,
                approval_id=match,
                reason="already_consumed_or_race",
            )
            msg = "Approval was already used or is no longer valid"
            raise GovernedApprovalMismatchError(msg)
        logger.info(
            EXTERNAL_API_APPROVAL_CONSUMED, connection=connection, approval_id=match
        )
        return None

    async def _find_matching_approval(
        self, signature: ActionSignature, approval_id: str | None, connection: str
    ) -> str | None:
        """Find an APPROVED, unconsumed approval matching this call.

        Returns:
            The matching approval id, or ``None`` to park.

        Raises:
            GovernedApprovalMismatchError: An explicit ``approval_id`` did
                not match this exact call (a replay/confusion signal).
        """
        if approval_id is not None:
            item = await self._approval_store.get(approval_id)
            if (
                item is None
                or item.status is not ApprovalStatus.APPROVED
                or item.consumed_at is not None
                or not self._bound_to_caller(item)
                or not signature.matches(ActionSignature.from_metadata(item.metadata))
            ):
                logger.warning(
                    EXTERNAL_API_SIGNATURE_MISMATCH,
                    connection=connection,
                    approval_id=approval_id,
                    reason="explicit_approval_no_match",
                )
                msg = "Supplied approval does not match this call or was already used"
                raise GovernedApprovalMismatchError(msg)
            return str(item.id)
        candidates = await self._approval_store.list_items(
            status=ApprovalStatus.APPROVED, action_type=self._action_type
        )
        for item in candidates:
            if (
                item.consumed_at is None
                and self._bound_to_caller(item)
                and signature.matches(ActionSignature.from_metadata(item.metadata))
            ):
                return str(item.id)
        return None

    def _bound_to_caller(self, item: ApprovalItem) -> bool:
        """Whether *item* was parked by this same agent + task.

        Returns:
            ``True`` when the grant belongs to this caller.
        """
        return item.requested_by == self._agent_id and item.task_id == self._task_id

    async def _park(
        self,
        signature: ActionSignature,
        connection: str,
        title: str,
        description: str,
    ) -> ToolExecutionResult:
        """Create a PENDING approval bound to this call and signal parking.

        Returns:
            The parking ``ToolExecutionResult``.
        """
        approval_id = str(uuid4())
        risk_level = self._classify_risk()
        item = ApprovalItem(
            id=UUID(approval_id),
            action_type=self._action_type,
            title=title,
            description=description,
            requested_by=self._agent_id,
            risk_level=risk_level,
            source=ApprovalSource.PARKED_CONTEXT,
            created_at=self._clock.now().astimezone(UTC),
            task_id=self._task_id,
            metadata=signature.to_metadata(),
        )
        await self._approval_store.add(item)
        logger.info(
            EXTERNAL_API_APPROVAL_REQUIRED,
            connection=connection,
            approval_id=approval_id,
            risk_level=risk_level.value,
        )
        return ToolExecutionResult(
            content=(
                f"Approval required (id={approval_id}). Execution is paused until"
                " a human approves; on approval, re-issue the same call to proceed."
            ),
            metadata={
                "requires_parking": True,
                "approval_id": approval_id,
                "action_type": self._action_type,
                "risk_level": risk_level.value,
            },
        )

    def _classify_risk(self) -> ApprovalRiskLevel:
        """Classify the call's risk, defaulting to HIGH when unavailable.

        Returns:
            The classified risk level.
        """
        if self._risk_classifier is None:
            return ApprovalRiskLevel.HIGH
        try:
            return self._risk_classifier.classify(self._action_type)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                EXTERNAL_API_RISK_CLASSIFY_FAILED,
                action_type=self._action_type,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="risk classification failed; defaulting to HIGH",
            )
            return ApprovalRiskLevel.HIGH


__all__ = [
    "ActionSignature",
    "ConnectionApprovalGate",
    "GovernedApprovalMismatchError",
]
