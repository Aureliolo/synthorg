"""Evidence Package schema for HITL approval.

``EvidencePackage`` is the structured payload carried by ``ApprovalItem``
and emitted as the content of ``APPROVAL_INTERRUPT`` /
``INFO_REQUEST_INTERRUPT`` SSE events.  It extends
``StructuredArtifact`` (shared base with ``HandoffArtifact``) to
prevent schema drift between role-transition handoffs and HITL
approvals.

Lives in ``core`` (not ``communication``) because ``ApprovalItem``
references it as a field type, and ``core`` must not depend on
``communication`` (which triggers a circular import chain).
"""

import copy
from datetime import datetime  # noqa: TC003
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.core.enums import ApprovalRiskLevel  # noqa: TC001
from synthorg.core.structured_artifact import StructuredArtifact
from synthorg.core.types import NotBlankStr  # noqa: TC001


class RecommendedAction(BaseModel):
    """A single action option presented to the human approver.

    Attributes:
        action_type: Semantic action key (e.g. ``"approve"``, ``"reject"``).
        label: Button text shown in the UI.
        description: Explanation of what this action does.
        confirmation_required: Whether the UI should show a confirmation
            dialog before executing.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    action_type: NotBlankStr = Field(description="Semantic action key")
    label: NotBlankStr = Field(description="UI button text")
    description: NotBlankStr = Field(description="Action explanation")
    confirmation_required: bool = Field(
        default=False,
        description="Whether to show a confirmation dialog",
    )


class EvidencePackageSignature(BaseModel):
    """Approver signature over an EvidencePackage.

    Produced by the audit chain using ML-DSA-65 (FIPS 204) or equivalent
    quantum-safe signature scheme.

    Attributes:
        approver_id: Identity of the approver.
        algorithm: Signature algorithm used.
        signature_bytes: Raw signature bytes.
        signed_at: When the signature was produced.
        chain_position: Position in the append-only audit chain.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    approver_id: NotBlankStr = Field(description="Approver identity")
    algorithm: Literal["ml-dsa-65", "ed25519"] = Field(
        description="Signature algorithm",
    )
    signature_bytes: bytes = Field(description="Raw signature bytes")
    signed_at: datetime = Field(description="Signature timestamp")
    chain_position: int = Field(
        ge=0,
        description="Position in the append-only audit chain",
    )


class EvidencePackage(StructuredArtifact):
    """Structured HITL approval artifact.

    Used as the payload for ``ApprovalItem`` and emitted as the content
    of ``APPROVAL_INTERRUPT`` / ``INFO_REQUEST_INTERRUPT`` SSE events.

    Attributes:
        id: Stable UUID for the evidence package.
        title: Short human-readable summary.
        narrative: 2-5 sentence plain-English explanation.
        reasoning_trace: Compressed reasoning steps (NOT full CoT).
        recommended_actions: 1-3 action options for the approver.
        source_agent_id: Agent that produced this package.
        task_id: Related task, if any.
        risk_level: Risk classification.
        metadata: Additional key-value metadata.
    """

    id: NotBlankStr = Field(description="Stable evidence package UUID")
    title: NotBlankStr = Field(description="Short human-readable summary")
    narrative: NotBlankStr = Field(
        description="Plain-English explanation of the approval context",
    )
    reasoning_trace: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Compressed reasoning steps",
    )
    recommended_actions: tuple[RecommendedAction, ...] = Field(
        min_length=1,
        max_length=3,
        description="Action options for the approver (1-3)",
    )
    source_agent_id: NotBlankStr = Field(
        description="Producing agent identifier",
    )
    task_id: NotBlankStr | None = Field(
        default=None,
        description="Related task identifier",
    )
    risk_level: ApprovalRiskLevel = Field(
        description="Risk classification",
    )
    metadata: dict[str, object] = Field(
        default_factory=dict,
        description="Additional key-value metadata",
    )
    signature_threshold: int = Field(
        default=1,
        ge=1,
        le=10,
        description="Minimum signatures required before action executes",
    )
    signatures: tuple[EvidencePackageSignature, ...] = Field(
        default=(),
        description="Collected approver signatures",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_fully_signed(self) -> bool:
        """Whether the required signature threshold has been met.

        Counts distinct approvers so a single approver cannot be
        counted multiple times.
        """
        distinct = {sig.approver_id for sig in self.signatures}
        return len(distinct) >= self.signature_threshold

    @model_validator(mode="after")
    def _validate_signature_uniqueness(self) -> Self:
        """Reject duplicate approver_id values in signatures."""
        ids = [sig.approver_id for sig in self.signatures]
        if len(ids) != len(set(ids)):
            msg = "signatures must contain unique approver_id values"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _deep_copy_metadata(self) -> Self:
        """Deep-copy metadata to prevent external mutation."""
        object.__setattr__(self, "metadata", copy.deepcopy(self.metadata))
        return self
