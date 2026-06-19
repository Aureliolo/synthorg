# module-kind: code
"""Domain models for the in-family model-upgrade recommender.

The inverse of the budget downgrade recommender: instead of moving an
agent to a cheaper model, this surfaces a newer model in the same
``metadata.family`` (higher ``metadata.generation``) so an operator can
upgrade.  ``UpgradeRecommendation`` is the pure recommendation;
``StoredUpgradeRecommendation`` wraps it with the lifecycle state the
persisted review/approve surface needs.
"""

from datetime import datetime
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.providers.enums import RecommendationStatus


class UpgradeRecommendation(BaseModel):
    """A newer-in-family model available to replace a configured model.

    Attributes:
        provider_name: Provider whose model could be upgraded.
        current_model_id: The configured (older-generation) model.
        recommended_model_id: The newer in-family model.
        family: The shared model family.
        current_generation: Generation of the current model.
        recommended_generation: Generation of the recommendation (newer).
        score: Confidence in [0, 1] derived from the matcher weights.
        reason: Human-readable explanation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    provider_name: NotBlankStr
    current_model_id: NotBlankStr
    recommended_model_id: NotBlankStr
    family: NotBlankStr
    current_generation: float = Field(ge=0.0)
    recommended_generation: float = Field(gt=0.0)
    score: float = Field(ge=0.0, le=1.0)
    reason: NotBlankStr

    @model_validator(mode="after")
    def _validate_upgrade(self) -> Self:
        """Ensure the recommendation is a genuine upgrade.

        Returns:
            The validated instance (``self``).

        Raises:
            ValueError: If the models are identical or the recommended
                generation is not strictly newer.
        """
        if self.current_model_id == self.recommended_model_id:
            msg = (
                "current_model_id and recommended_model_id must differ, "
                f"both are {self.current_model_id!r}"
            )
            raise ValueError(msg)
        if self.recommended_generation <= self.current_generation:
            msg = (
                f"recommended_generation ({self.recommended_generation}) must "
                f"exceed current_generation ({self.current_generation})"
            )
            raise ValueError(msg)
        return self


class UpgradeAnalysis(BaseModel):
    """Result of an upgrade-recommendation scan over configured providers.

    Attributes:
        recommendations: Per-model upgrade recommendations.
        recommendation_count: Number of recommendations (computed).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    recommendations: tuple[UpgradeRecommendation, ...] = Field(default=())

    @computed_field
    @property
    def recommendation_count(self) -> int:
        """Number of recommendations in this analysis."""
        return len(self.recommendations)

    @model_validator(mode="after")
    def _no_duplicate_recommendations(self) -> Self:
        """Reject duplicate recommendations for the same upgrade.

        A single scan must not surface the same
        ``(provider, current, recommended)`` upgrade twice, which would
        otherwise persist as duplicate pending rows.

        Returns:
            The validated analysis.

        Raises:
            ValueError: If two recommendations share an upgrade key.
        """
        keys = [
            (r.provider_name, r.current_model_id, r.recommended_model_id)
            for r in self.recommendations
        ]
        if len(keys) != len(set(keys)):
            msg = "UpgradeAnalysis contains duplicate recommendations"
            raise ValueError(msg)
        return self


class StoredUpgradeRecommendation(BaseModel):
    """A persisted upgrade recommendation with review lifecycle state.

    Attributes:
        id: Stable primary key.
        recommendation: The underlying recommendation.
        agent_ids: Agents currently pinned to the current model.
        status: Lifecycle state.
        created_at: When the recommendation was first persisted.
        decided_at: When it was approved/rejected/auto-applied, if at all.
        decided_by: Who decided, if a human did.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    id: UUID = Field(default_factory=uuid4)
    recommendation: UpgradeRecommendation
    agent_ids: tuple[NotBlankStr, ...] = Field(default=())
    status: RecommendationStatus = RecommendationStatus.PENDING
    created_at: datetime
    decided_at: datetime | None = None
    decided_by: NotBlankStr | None = None

    @model_validator(mode="after")
    def _decision_matches_status(self) -> Self:
        """Enforce the status<->decision-stamp correlation.

        A decided status (approved / rejected / auto-applied) must carry a
        ``decided_at``; a pending recommendation must carry neither
        ``decided_at`` nor ``decided_by``. Makes the illegal "approved but
        undecided" state unrepresentable rather than doc-only.

        Returns:
            The validated recommendation.

        Raises:
            ValueError: If the status and decision stamps disagree.
        """
        decided = {
            RecommendationStatus.APPROVED,
            RecommendationStatus.REJECTED,
            RecommendationStatus.AUTO_APPLIED,
        }
        if self.status in decided:
            # Both stamps are required so a decided record always carries
            # actor attribution, not only a timestamp; the persistence
            # CHECK enforces the same pairing at the storage boundary.
            if self.decided_at is None:
                msg = f"decided_at is required for status {self.status.value!r}"
                raise ValueError(msg)
            if self.decided_by is None:
                msg = f"decided_by is required for status {self.status.value!r}"
                raise ValueError(msg)
        if self.status is RecommendationStatus.PENDING and (
            self.decided_at is not None or self.decided_by is not None
        ):
            msg = "pending recommendations must not carry decided_at/decided_by"
            raise ValueError(msg)
        return self
