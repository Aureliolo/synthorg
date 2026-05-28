"""Coordination feature state slice.

Holds the coordination-metrics store (per-run multi-agent coordination
signals), the coordination service, and the ceremony-policy service.
All ``None`` until wired; the coordination controllers raise 503 on a
``None`` field.
"""

from typing import TYPE_CHECKING

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg.budget.coordination_store import (
    CoordinationMetricsStore,
)
from synthorg.coordination.ceremony_policy.service import (
    CeremonyPolicyService,
)
from synthorg.coordination.service import CoordinationService

if TYPE_CHECKING:
    from synthorg.api.state_slices import AppStateSliceMixin


class CoordinationStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the coordination feature."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    metrics_store: CoordinationMetricsStore | None = None
    coordination_service: CoordinationService | None = None
    ceremony_policy_service: CeremonyPolicyService | None = None


def coordination_metrics_store_of(
    app_state: AppStateSliceMixin,
) -> CoordinationMetricsStore:
    """Resolve the coordination metrics store from its slice, or raise 503.

    Returns:
        The wired coordination metrics store.
    """
    return require_service(
        app_state.slice(CoordinationStateSlice).metrics_store,
        "Coordination Metrics Store",
    )


def coordination_service_of(app_state: AppStateSliceMixin) -> CoordinationService:
    """Resolve the coordination service from its slice, or raise 503.

    Returns:
        The wired coordination service.
    """
    return require_service(
        app_state.slice(CoordinationStateSlice).coordination_service,
        "Coordination Service",
    )


def ceremony_policy_service_of(
    app_state: AppStateSliceMixin,
) -> CeremonyPolicyService:
    """Resolve the ceremony policy service from its slice, or raise 503.

    Returns:
        The wired ceremony policy service.
    """
    return require_service(
        app_state.slice(CoordinationStateSlice).ceremony_policy_service,
        "Ceremony Policy Service",
    )
