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

from synthorg.config.provider_schema import ProviderConfig
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_MODEL_FLAGGED_STALE,
    PROVIDER_MODEL_REFRESH_ADD_FAILED,
    PROVIDER_MODEL_REFRESH_RECOMMEND_FAILED,
)
from synthorg.providers.management.dtos import UpdateProviderRequest
from synthorg.providers.management.live_discovery_probe import (
    LiveCatalogReport,
    LiveDiscoveryProbe,
)
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
        recommendations_valid: ``True`` when the recommend step ran to
            completion (so ``recommendations`` is the authoritative current
            set the caller may reconcile pending rows against); ``False``
            when it failed, so an empty ``recommendations`` reflects an error
            rather than "nothing to recommend" and must not drive retirement.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    provider_name: NotBlankStr
    added_ids: tuple[str, ...] = Field(default=())
    stale_ids: tuple[str, ...] = Field(default=())
    recommendations: tuple[UpgradeRecommendation, ...] = Field(default=())
    recommendations_valid: bool = Field(default=True)


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

    async def flag(
        self, provider_name: str, missing_ids: tuple[str, ...]
    ) -> tuple[str, ...]:
        """Flag *missing_ids* stale; never raises (best-effort).

        The underlying ``flag_models_stale`` write is atomic for the
        batch, so the outcome is all-or-nothing.

        Returns:
            ``missing_ids`` when the flag write succeeded, or an empty
            tuple when it failed, so callers report only ids actually
            persisted as stale rather than every id they hoped to flag.
        """
        if not missing_ids:
            return ()
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
            return ()
        return missing_ids


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
        stale_ids = await self._flagger.flag(provider_name, report.missing_ids)
        return ProviderRefreshOutcome(
            provider_name=provider_name,
            stale_ids=stale_ids,
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

        Persists newly-discovered models (stamped ``probe`` provenance) and
        refreshes the discovery-sourced metadata of models still advertised,
        flags removed configured ids stale, then recommends in-family
        upgrades against the refreshed provider config.

        Returns:
            The per-provider outcome with added ids, stale ids, and
            recommendations.
        """
        report = await self._probe.discover_report(provider_name, provider)
        added_ids = await self._refresh_catalog(provider_name, provider, report)
        stale_ids = await self._flagger.flag(provider_name, report.missing_ids)

        recommendations = await self._recommend(provider_name)
        return ProviderRefreshOutcome(
            provider_name=provider_name,
            added_ids=added_ids,
            stale_ids=stale_ids,
            recommendations=recommendations or (),
            recommendations_valid=recommendations is not None,
        )

    async def _refresh_catalog(
        self,
        provider_name: str,
        provider: ProviderConfig,
        report: LiveCatalogReport,
    ) -> tuple[str, ...]:
        """Persist new models and refresh live models' discovery metadata.

        A configured model still advertised by the catalogue has its
        discovery-sourced ``metadata`` (family / generation / capabilities)
        refreshed, so parser or enrichment improvements propagate to the
        matcher and the upgrade recommender without waiting for the model to
        be removed and re-added; operator per-model fields (``local_params``,
        cost overrides) and any stale marker are preserved. Configured models
        absent from the catalogue are kept verbatim for the stale-flag pass.
        The refresh is one atomic persist, skipped when nothing changed so an
        unchanged catalogue never churns the config or the audit log.

        Returns:
            The ids the catalogue surfaced as new (empty when discovery was a
            no-op or the persist failed).
        """
        if not report.discovered:
            return ()
        discovered_by_id = {m.id: m for m in report.discovered}
        configured_ids = {m.id for m in provider.models}
        refreshed = tuple(
            model.model_copy(update={"metadata": discovered_by_id[model.id].metadata})
            if model.id in discovered_by_id and model.stale is None
            else model
            for model in provider.models
        )
        merged = refreshed + tuple(
            m for m in report.discovered if m.id not in configured_ids
        )
        if merged == provider.models:
            return report.added_ids
        try:
            await self._mgmt.update_provider(
                provider_name, UpdateProviderRequest(models=merged)
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                PROVIDER_MODEL_REFRESH_ADD_FAILED,
                provider=provider_name,
                note="catalog_refresh_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ()
        return report.added_ids

    async def _recommend(
        self, provider_name: str
    ) -> tuple[UpgradeRecommendation, ...] | None:
        """Re-read the refreshed provider and recommend in-family upgrades.

        Isolates a failure of the post-add re-read / recommend step (e.g.
        a concurrent provider deletion) so it does not lose the stale
        flags already applied this pass.

        Returns:
            The produced recommendations (possibly an empty tuple when the
            catalogue has no in-family upgrade), or ``None`` when the step
            failed -- distinguished so the caller never treats an error as an
            authoritative empty set that would retire live recommendations.
        """
        try:
            refreshed = await self._mgmt.get_provider(provider_name)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                PROVIDER_MODEL_REFRESH_RECOMMEND_FAILED,
                provider=provider_name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None
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
