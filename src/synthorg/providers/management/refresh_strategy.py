# module-kind: code
"""Pluggable model-refresh strategies.

The config discriminator (:class:`RefreshMode`) selects a strategy via
:func:`build_refresh_strategy`. ``OFF`` / ``MANUAL_ONLY`` map to ``None``
(nothing is scheduled). ``DETECT_ONLY`` flags removed models stale but
never persists new models or recommends; ``RECONCILE_RECOMMEND``
additionally persists newly-discovered models and produces upgrade
recommendations. Strategies only detect/reconcile + recommend; the
service owns persisting recommendations and (api-layer) auto-apply, so
this module never reaches up into the api layer.
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_MODEL_FLAGGED_STALE,
    PROVIDER_MODEL_REFRESH_ADD_FAILED,
)
from synthorg.providers.management.capability_dtos import AddModelRequest
from synthorg.providers.management.live_discovery_probe import LiveDiscoveryProbe
from synthorg.providers.management.refresh_config import RefreshMode
from synthorg.providers.management.service import ProviderManagementService
from synthorg.providers.management.upgrade_models import UpgradeRecommendation
from synthorg.providers.management.upgrade_recommender import UpgradeRecommender

logger = get_logger(__name__)


class ProviderRefreshOutcome(BaseModel):
    """Per-provider result of one reconcile pass.

    Attributes:
        provider_name: The reconciled provider.
        added_ids: Newly-discovered model ids persisted this pass.
        stale_ids: Configured ids flagged stale this pass.
        recommendations: Upgrade recommendations produced this pass.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    provider_name: NotBlankStr
    added_ids: tuple[str, ...] = Field(default=())
    stale_ids: tuple[str, ...] = Field(default=())
    recommendations: tuple[UpgradeRecommendation, ...] = Field(default=())


@runtime_checkable
class RefreshStrategy(Protocol):
    """Reconciles one provider's catalogue against its live source."""

    async def reconcile(
        self,
        provider_name: str,
        provider: ProviderConfig,
    ) -> ProviderRefreshOutcome:
        """Reconcile *provider* and return what changed."""
        ...


class _StaleFlagger:
    """Shared logic for flagging configured ids absent from the catalogue."""

    def __init__(self, mgmt_service: ProviderManagementService, clock: Clock) -> None:
        self._mgmt = mgmt_service
        self._clock = clock

    async def flag(self, provider_name: str, missing_ids: tuple[str, ...]) -> None:
        """Flag *missing_ids* stale; never raises (best-effort)."""
        if not missing_ids:
            return
        try:
            await self._mgmt.flag_models_stale(
                provider_name,
                stale_ids=missing_ids,
                reason="removed_from_catalog",
                flagged_at=self._clock.now(),
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                PROVIDER_MODEL_FLAGGED_STALE,
                provider=provider_name,
                note="flag_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )


class DetectOnlyStrategy:
    """Flags removed models stale; never persists new models or recommends."""

    def __init__(
        self,
        *,
        probe: LiveDiscoveryProbe,
        mgmt_service: ProviderManagementService,
        clock: Clock | None = None,
    ) -> None:
        self._probe = probe
        self._mgmt = mgmt_service
        self._clock = clock or SystemClock()
        self._flagger = _StaleFlagger(mgmt_service, self._clock)

    async def reconcile(
        self,
        provider_name: str,
        provider: ProviderConfig,
    ) -> ProviderRefreshOutcome:
        """Probe the live catalogue and flag any absent configured ids.

        Returns:
            The per-provider outcome (no added models, no recommendations).
        """
        report = await self._probe.discover_report(provider_name, provider)
        await self._flagger.flag(provider_name, report.missing_ids)
        return ProviderRefreshOutcome(
            provider_name=provider_name,
            stale_ids=report.missing_ids,
        )


class ReconcileRecommendStrategy:
    """Persists newly-discovered models, flags stale, and recommends upgrades."""

    def __init__(
        self,
        *,
        probe: LiveDiscoveryProbe,
        mgmt_service: ProviderManagementService,
        recommender: UpgradeRecommender,
        clock: Clock | None = None,
    ) -> None:
        self._probe = probe
        self._mgmt = mgmt_service
        self._recommender = recommender
        self._clock = clock or SystemClock()
        self._flagger = _StaleFlagger(mgmt_service, self._clock)

    async def reconcile(
        self,
        provider_name: str,
        provider: ProviderConfig,
    ) -> ProviderRefreshOutcome:
        """Reconcile the catalogue and recommend in-family upgrades.

        Persists newly-discovered models (stamped ``probe`` provenance),
        flags removed configured ids stale, then recommends in-family
        upgrades against the refreshed provider config.

        Returns:
            The per-provider outcome with added ids, stale ids, and
            recommendations.
        """
        report = await self._probe.discover_report(provider_name, provider)
        discovered_by_id = {m.id: m for m in report.discovered}
        added_ids = await self._add_discovered(
            provider_name, report.added_ids, discovered_by_id
        )
        await self._flagger.flag(provider_name, report.missing_ids)

        recommendations = await self._recommend(provider_name)
        return ProviderRefreshOutcome(
            provider_name=provider_name,
            added_ids=added_ids,
            stale_ids=report.missing_ids,
            recommendations=recommendations,
        )

    async def _add_discovered(
        self,
        provider_name: str,
        added_ids: tuple[str, ...],
        discovered_by_id: dict[str, ProviderModelConfig],
    ) -> tuple[str, ...]:
        """Persist each newly-discovered model, isolating per-model failures.

        A single bad model id (validation error, transient persistence
        error) must not abort the whole provider's reconcile, so each
        ``add_model`` is guarded and the returned tuple reflects only the
        ids that were actually persisted.

        Returns:
            The subset of *added_ids* that were successfully persisted.
        """
        persisted: list[str] = []
        for added_id in added_ids:
            try:
                await self._mgmt.add_model(
                    provider_name,
                    AddModelRequest(model=discovered_by_id[added_id]),
                )
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    PROVIDER_MODEL_REFRESH_ADD_FAILED,
                    provider=provider_name,
                    model=added_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                continue
            persisted.append(added_id)
        return tuple(persisted)

    async def _recommend(self, provider_name: str) -> tuple[UpgradeRecommendation, ...]:
        """Re-read the refreshed provider and recommend in-family upgrades.

        Isolates a failure of the post-add re-read / recommend step (e.g.
        a concurrent provider deletion) so it does not lose the stale
        flags already applied this pass.

        Returns:
            The produced recommendations, or an empty tuple on failure.
        """
        try:
            refreshed = await self._mgmt.get_provider(provider_name)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                PROVIDER_MODEL_REFRESH_ADD_FAILED,
                provider=provider_name,
                note="recommend_reread_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ()
        analysis = self._recommender.recommend({provider_name: refreshed})
        return analysis.recommendations


def build_refresh_strategy(
    mode: RefreshMode,
    *,
    probe: LiveDiscoveryProbe,
    mgmt_service: ProviderManagementService,
    recommender: UpgradeRecommender,
    clock: Clock | None = None,
) -> RefreshStrategy | None:
    """Return the strategy for *mode*, or ``None`` when nothing is scheduled.

    Returns:
        A :class:`RefreshStrategy` for ``DETECT_ONLY`` /
        ``RECONCILE_RECOMMEND``; ``None`` for ``OFF`` / ``MANUAL_ONLY``.
    """
    if mode is RefreshMode.DETECT_ONLY:
        return DetectOnlyStrategy(probe=probe, mgmt_service=mgmt_service, clock=clock)
    if mode is RefreshMode.RECONCILE_RECOMMEND:
        return ReconcileRecommendStrategy(
            probe=probe,
            mgmt_service=mgmt_service,
            recommender=recommender,
            clock=clock,
        )
    return None


__all__ = [
    "DetectOnlyStrategy",
    "ProviderRefreshOutcome",
    "ReconcileRecommendStrategy",
    "RefreshStrategy",
    "build_refresh_strategy",
]
