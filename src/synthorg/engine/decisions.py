"""Auditable decision records for the approval gate.

Immutable, append-only records of every approval gate decision. Each
record captures full context at decision time -- executing agent,
reviewer, criteria snapshot, and outcome -- for audit and analytics.

See the Review Gate section of ``docs/design/engine.md`` for the
drop-box design rationale, and ``docs/design/security.md`` for how
approval decisions flow through the lifecycle.
"""

from enum import StrEnum
from types import MappingProxyType
from typing import Self
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from synthorg.core.types import NotBlankStr, validate_unique_strings
from synthorg.engine.immutable import deep_copy_mapping, freeze_recursive
from synthorg.engine.strategy.models import (
    ConfidenceMetadata,
    LensAttribution,
    RiskCard,
)
from synthorg.ontology.decorator import ontology_entity


class DecisionOutcome(StrEnum):
    """Outcome of a review gate decision.

    Used by ``DecisionRecord`` for the auditable decisions drop-box.
    """

    APPROVED = "approved"
    REJECTED = "rejected"
    AUTO_APPROVED = "auto_approved"
    AUTO_REJECTED = "auto_rejected"
    ESCALATED = "escalated"


@ontology_entity
class DecisionRecord(BaseModel):
    """Immutable record of a review gate decision.

    Attributes:
        id: Unique decision record identifier (UUID).
        task_id: Task that was reviewed.
        approval_id: Associated ``ApprovalItem`` ID (``None`` for
            programmatic decisions without an explicit approval item).
        executing_agent_id: Agent that performed the work.
        reviewer_agent_id: Agent or human that reviewed.  Must differ
            from ``executing_agent_id`` -- no-self-review is a type
            invariant, not just a service-layer policy.
        decision: The outcome of the review.
        reason: Optional rationale for the decision.  Empty or
            whitespace-only strings are coerced to ``None`` at
            construction so the model never carries a tri-state
            ("", None, populated).
        criteria_snapshot: Acceptance criteria at decision time (empty
            tuple when the task has no acceptance criteria).  Each
            element must be non-blank.
        recorded_at: When the decision was recorded.
        version: Monotonic version per task (1-indexed).  Server-
            assigned by the persistence layer; the service never picks
            the value itself to avoid TOCTOU races.
        metadata: Forward-compatible structured metadata (deep-copied
            AND wrapped in ``MappingProxyType`` at construction to
            block post-construction mutation of the audit record).
    """

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        arbitrary_types_allowed=True,
        extra="forbid",
    )

    id: UUID = Field(
        default_factory=uuid4,
        description="Unique decision record identifier",
    )
    task_id: NotBlankStr = Field(description="Task that was reviewed")
    approval_id: NotBlankStr | None = Field(
        default=None,
        description="Associated ApprovalItem identifier",
    )
    executing_agent_id: NotBlankStr = Field(
        description="Agent that performed the work",
    )
    reviewer_agent_id: NotBlankStr = Field(
        description="Agent or human that reviewed",
    )
    decision: DecisionOutcome = Field(description="Outcome of the review")
    reason: NotBlankStr | None = Field(
        default=None,
        description=(
            "Optional rationale for the decision; empty or whitespace-only "
            "strings are coerced to None at construction."
        ),
    )
    criteria_snapshot: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Acceptance criteria at decision time (unique)",
    )
    recorded_at: AwareDatetime = Field(description="When the decision was recorded")
    version: int = Field(ge=1, description="Monotonic version per task")
    metadata: MappingProxyType[str, object] = Field(
        default_factory=lambda: MappingProxyType({}),
        description="Forward-compatible metadata (read-only view)",
    )
    risk_card: RiskCard | None = Field(
        default=None,
        description="Risk assessment from premortem phase (if available)",
    )
    confidence_metadata: ConfidenceMetadata | None = Field(
        default=None,
        description="Confidence calibration from strategic analysis (if available)",
    )
    lens_attribution: tuple[LensAttribution, ...] = Field(
        default=(),
        description="Strategic lens attributions used in analysis",
    )

    @field_validator("reason", mode="before")
    @classmethod
    def _coerce_empty_reason_to_none(cls, value: object) -> object:
        """Normalize empty / whitespace-only reasons to ``None``.

        Returns:
            ``None`` when ``value`` is an empty / whitespace-only
            string; the original value otherwise.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("metadata", mode="before")
    @classmethod
    def _deep_copy_and_freeze_metadata(cls, value: object) -> object:
        """Deep-copy and recursively freeze metadata.

        Wrapping the top-level mapping in ``MappingProxyType`` blocks
        ``record.metadata["key"] = ...``; recursing into nested
        containers also blocks ``record.metadata["nested"]["key"] =
        ...`` by converting ``dict`` -> ``MappingProxyType``,
        ``list`` -> ``tuple``, and ``set`` -> ``frozenset``.  Together
        these preserve the append-only audit-record contract even for
        forward-compatible metadata payloads that carry nested
        structures.  If the input is already a ``MappingProxyType``
        (e.g. passed to ``model_copy``), we rebuild from the
        underlying dict so the deep-copy + freeze steps still run.

        Returns:
            A deeply-copied, recursively-frozen view of ``value``.
        """
        if isinstance(value, MappingProxyType):
            # Unwrap so deep_copy_mapping sees a dict and actually
            # produces an independent copy before we re-wrap.
            value = dict(value)
        copied = deep_copy_mapping(value)
        return freeze_recursive(copied)

    @field_validator("criteria_snapshot", mode="after")
    @classmethod
    def _validate_criteria_snapshot_unique(
        cls,
        value: tuple[NotBlankStr, ...],
    ) -> tuple[NotBlankStr, ...]:
        """Reject duplicate criteria -- they represent unique rules.

        Returns:
            ``value`` unchanged when every criterion is unique.
        """
        validate_unique_strings(value, "criteria_snapshot")
        return value

    @model_validator(mode="after")
    def _forbid_self_review(self) -> Self:
        """Enforce no-self-review as a type-level invariant.

        Returns:
            ``self`` unchanged when executor and reviewer differ.

        Raises:
            ValueError: When ``executing_agent_id`` equals
                ``reviewer_agent_id`` (a self-review).
        """
        if self.executing_agent_id == self.reviewer_agent_id:
            msg = (
                f"executing_agent_id and reviewer_agent_id must differ "
                f"(got {self.executing_agent_id!r} for both)"
            )
            raise ValueError(msg)
        return self
