# module-kind: service
"""Periodic model-refresh / reconcile orchestration service.

Iterates the configured providers, runs the mode-selected
:class:`RefreshStrategy` against each, persists produced upgrade
recommendations (deduped against existing pending rows), and -- when the
opt-in in-family auto-apply flag is set -- invokes an injected apply hook
so the api layer reassigns pinned agents (keeping this providers-layer
service free of any upward api import).
"""

from collections.abc import Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_MODEL_REFRESH_CYCLE_RAN,
    PROVIDER_MODEL_REFRESH_PROVIDER_FAILED,
    PROVIDER_MODEL_UPGRADE_RECOMMENDED,
)
from synthorg.persistence.upgrade_recommendation_protocol import (
    UpgradeRecommendationFilterSpec,
    UpgradeRecommendationRepository,
)
from synthorg.providers.enums import RecommendationStatus
from synthorg.providers.management.live_discovery_probe import LiveDiscoveryProbe
from synthorg.providers.management.refresh_config import RefreshMode
from synthorg.providers.management.refresh_strategy import build_refresh_strategy
from synthorg.providers.management.service import ProviderManagementService
from synthorg.providers.management.upgrade_models import (
    StoredUpgradeRecommendation,
    UpgradeRecommendation,
)
from synthorg.providers.management.upgrade_recommender import UpgradeRecommender
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

ApplyHook = Callable[[StoredUpgradeRecommendation], Awaitable[None]]
"""Api-layer callback that reassigns pinned agents for an auto-apply."""

_PENDING_SCAN_LIMIT: int = 1_000


class RefreshCycleReport(BaseModel):
    """Aggregate outcome of one refresh cycle across all providers.

    Attributes:
        providers_scanned: Number of providers reconciled.
        added_count: Newly-discovered models persisted.
        stale_count: Configured ids flagged stale.
        recommended_count: New recommendations persisted this cycle.
        auto_applied_count: Recommendations auto-applied this cycle.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    providers_scanned: int = Field(default=0, ge=0)
    added_count: int = Field(default=0, ge=0)
    stale_count: int = Field(default=0, ge=0)
    recommended_count: int = Field(default=0, ge=0)
    auto_applied_count: int = Field(default=0, ge=0)


class ModelRefreshService:
    """Drives a single reconcile pass over every configured provider."""

    def __init__(  # noqa: PLR0913 -- collaborator seams are injected explicitly
        self,
        *,
        mgmt_service: ProviderManagementService,
        probe: LiveDiscoveryProbe,
        recommender: UpgradeRecommender,
        repo: UpgradeRecommendationRepository,
        config_resolver: ConfigResolver,
        clock: Clock | None = None,
    ) -> None:
        """Initialise the service.

        Args:
            mgmt_service: Provider management service (catalog + mutations).
            probe: Live-discovery presence probe.
            recommender: In-family upgrade recommender.
            repo: Durable recommendation store.
            config_resolver: Reads provider + agent config.
            clock: Clock seam; defaults to ``SystemClock``.
        """
        self._mgmt = mgmt_service
        self._probe = probe
        self._recommender = recommender
        self._repo = repo
        self._config_resolver = config_resolver
        self._clock = clock or SystemClock()

    async def run_cycle(
        self,
        *,
        mode: RefreshMode,
        auto_apply: bool = False,
        apply_recommendation: ApplyHook | None = None,
    ) -> RefreshCycleReport:
        """Run one reconcile pass under *mode*.

        Args:
            mode: The effective refresh mode (``OFF`` / ``MANUAL_ONLY``
                return an empty report). A manual trigger passes
                ``RECONCILE_RECOMMEND`` to force a full pass.
            auto_apply: When set, qualifying in-family recommendations are
                auto-applied via *apply_recommendation*.
            apply_recommendation: Api-layer hook reassigning pinned agents
                for an auto-applied recommendation.

        Returns:
            The aggregate :class:`RefreshCycleReport`.
        """
        strategy = build_refresh_strategy(
            mode,
            probe=self._probe,
            mgmt_service=self._mgmt,
            recommender=self._recommender,
            clock=self._clock,
        )
        if strategy is None:
            return RefreshCycleReport()

        providers = await self._mgmt.list_providers()
        seen_pending = await self._existing_pending_keys()
        agents = await self._config_resolver.get_agents()

        added = stale = recommended = auto_applied = 0
        scanned = 0
        for provider_name, provider in providers.items():
            try:
                outcome = await strategy.reconcile(provider_name, provider)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    PROVIDER_MODEL_REFRESH_PROVIDER_FAILED,
                    provider=provider_name,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                continue
            scanned += 1
            added += len(outcome.added_ids)
            stale += len(outcome.stale_ids)
            for rec in outcome.recommendations:
                key = (
                    rec.provider_name,
                    rec.current_model_id,
                    rec.recommended_model_id,
                )
                if key in seen_pending:
                    continue
                seen_pending.add(key)
                try:
                    stored = await self._persist(rec, agents)
                    recommended += 1
                    if auto_apply and apply_recommendation is not None:
                        await apply_recommendation(stored)
                        auto_applied += 1
                except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                    reraise_critical(exc)
                    logger.warning(
                        PROVIDER_MODEL_REFRESH_PROVIDER_FAILED,
                        provider=provider_name,
                        note="recommendation_persist_or_apply_failed",
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )

        report = RefreshCycleReport(
            providers_scanned=scanned,
            added_count=added,
            stale_count=stale,
            recommended_count=recommended,
            auto_applied_count=auto_applied,
        )
        logger.info(
            PROVIDER_MODEL_REFRESH_CYCLE_RAN,
            providers_scanned=scanned,
            added_count=added,
            stale_count=stale,
            recommended_count=recommended,
            auto_applied_count=auto_applied,
        )
        return report

    async def _existing_pending_keys(self) -> set[tuple[str, str, str]]:
        """Return keys of pending recommendations to dedup against.

        Returns:
            A set of ``(provider, current_id, recommended_id)`` for every
            currently-pending recommendation.
        """
        pending = await self._repo.query(
            UpgradeRecommendationFilterSpec(status=RecommendationStatus.PENDING),
            limit=_PENDING_SCAN_LIMIT,
        )
        return {
            (
                row.recommendation.provider_name,
                row.recommendation.current_model_id,
                row.recommendation.recommended_model_id,
            )
            for row in pending
        }

    async def _persist(
        self,
        rec: UpgradeRecommendation,
        agents: tuple[object, ...],
    ) -> StoredUpgradeRecommendation:
        """Persist *rec* as a pending recommendation with pinned agents.

        Returns:
            The stored recommendation.
        """
        agent_ids = _pinned_agent_ids(agents, rec)
        stored = StoredUpgradeRecommendation(
            recommendation=rec,
            agent_ids=agent_ids,
            status=RecommendationStatus.PENDING,
            created_at=self._clock.now(),
        )
        await self._repo.save(stored)
        logger.info(
            PROVIDER_MODEL_UPGRADE_RECOMMENDED,
            provider=rec.provider_name,
            current_model=rec.current_model_id,
            recommended_model=rec.recommended_model_id,
            pinned_agents=len(agent_ids),
        )
        return stored


def _pinned_agent_ids(
    agents: tuple[object, ...],
    rec: UpgradeRecommendation,
) -> tuple[str, ...]:
    """Return the names of agents pinned to the recommendation's current model.

    Returns:
        Agent names whose configured model matches ``rec``'s provider +
        current model id.
    """
    names: list[str] = []
    for agent in agents:
        model = getattr(agent, "model", {})
        if not isinstance(model, dict):
            continue
        if (
            model.get("provider") == rec.provider_name
            and model.get("model_id") == rec.current_model_id
        ):
            name = getattr(agent, "name", None)
            if isinstance(name, str) and name.strip():
                names.append(name)
    return tuple(names)


__all__ = ["ApplyHook", "ModelRefreshService", "RefreshCycleReport"]
