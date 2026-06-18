"""Wire DTOs for the model-refresh + upgrade-recommendation surface."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.providers.enums import RecommendationStatus
from synthorg.providers.management.model_refresh_service import RefreshCycleReport
from synthorg.providers.management.refresh_config import (
    _MAX_REFRESH_INTERVAL_SECONDS,
    _MIN_REFRESH_INTERVAL_SECONDS,
    ModelRefreshConfig,
    RefreshMode,
)
from synthorg.providers.management.upgrade_models import StoredUpgradeRecommendation


class UpgradeRecommendationDTO(BaseModel):
    """A persisted upgrade recommendation on the wire."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    id: NotBlankStr
    provider_name: NotBlankStr
    current_model_id: NotBlankStr
    recommended_model_id: NotBlankStr
    family: NotBlankStr
    current_generation: float = Field(ge=0.0)
    recommended_generation: float = Field(gt=0.0)
    score: float = Field(ge=0.0, le=1.0)
    reason: NotBlankStr
    agent_ids: tuple[NotBlankStr, ...]
    status: RecommendationStatus
    created_at: datetime
    decided_at: datetime | None
    decided_by: NotBlankStr | None

    @classmethod
    def from_entity(
        cls, entity: StoredUpgradeRecommendation
    ) -> UpgradeRecommendationDTO:
        """Project a stored recommendation onto the wire DTO.

        Returns:
            The wire representation.
        """
        rec = entity.recommendation
        return cls(
            id=str(entity.id),
            provider_name=rec.provider_name,
            current_model_id=rec.current_model_id,
            recommended_model_id=rec.recommended_model_id,
            family=rec.family,
            current_generation=rec.current_generation,
            recommended_generation=rec.recommended_generation,
            score=rec.score,
            reason=rec.reason,
            agent_ids=entity.agent_ids,
            status=entity.status,
            created_at=entity.created_at,
            decided_at=entity.decided_at,
            decided_by=entity.decided_by,
        )


class RefreshCycleReportDTO(BaseModel):
    """Aggregate outcome of a manual refresh cycle on the wire."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    providers_scanned: int = Field(ge=0)
    added_count: int = Field(ge=0)
    stale_count: int = Field(ge=0)
    recommended_count: int = Field(ge=0)
    auto_applied_count: int = Field(ge=0)

    @classmethod
    def from_report(cls, report: RefreshCycleReport) -> RefreshCycleReportDTO:
        """Project a cycle report onto the wire DTO.

        Returns:
            The wire representation.
        """
        return cls(
            providers_scanned=report.providers_scanned,
            added_count=report.added_count,
            stale_count=report.stale_count,
            recommended_count=report.recommended_count,
            auto_applied_count=report.auto_applied_count,
        )


class RefreshStatusDTO(BaseModel):
    """Current model-refresh configuration on the wire."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    mode: RefreshMode
    interval_seconds: float = Field(
        ge=_MIN_REFRESH_INTERVAL_SECONDS,
        le=_MAX_REFRESH_INTERVAL_SECONDS,
    )
    auto_apply_within_family: bool

    @classmethod
    def from_config(cls, config: ModelRefreshConfig) -> RefreshStatusDTO:
        """Project the refresh config onto the wire DTO.

        Returns:
            The wire representation.
        """
        return cls(
            mode=config.mode,
            interval_seconds=config.interval_seconds,
            auto_apply_within_family=config.auto_apply_within_family,
        )
