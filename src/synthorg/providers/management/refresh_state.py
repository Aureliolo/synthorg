"""Model-refresh feature state slice.

Holds the periodic refresh service, its scheduler, and the durable
upgrade-recommendation repository. All ``None`` until wired at boot;
the model-refresh boot hook skips wiring entirely when the mode is
``off`` (the safe default), and the controllers 503 when a needed
collaborator is absent. A scheduler is only wired for the cadence modes
(``detect_only`` / ``reconcile_recommend``), so a wired scheduler always
implies a wired service, but a service may exist without a scheduler
(``manual_only``: on-demand refresh, no cadence).
"""

from typing import Self

from pydantic import ConfigDict, model_validator

from synthorg._core.features import BaseFeatureStateSlice
from synthorg.persistence.upgrade_recommendation_protocol import (
    UpgradeRecommendationRepository,
)
from synthorg.providers.management.model_refresh_service import ModelRefreshService
from synthorg.providers.management.refresh_scheduler import ModelRefreshScheduler


class ModelRefreshStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the model-refresh feature."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    service: ModelRefreshService | None = None
    scheduler: ModelRefreshScheduler | None = None
    recommendation_repo: UpgradeRecommendationRepository | None = None

    @model_validator(mode="after")
    def _scheduler_implies_service(self) -> Self:
        """A wired scheduler must have a service to drive.

        Returns:
            The validated instance (``self``).

        Raises:
            ValueError: When a scheduler is set without a service.
        """
        if self.scheduler is not None and self.service is None:
            msg = "ModelRefreshStateSlice.scheduler set without a service"
            raise ValueError(msg)
        return self
