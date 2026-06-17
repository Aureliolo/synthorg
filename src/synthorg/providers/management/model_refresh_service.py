# module-kind: service
"""Periodic model-refresh / reconcile orchestration service.

Iterates the configured providers, runs the mode-selected
:class:`RefreshStrategy` against each, persists produced upgrade
recommendations (deduped against existing pending rows), and -- when the
opt-in in-family auto-apply flag is set -- invokes an injected apply hook
so the api layer reassigns pinned agents (keeping this providers-layer
service free of any upward api import).
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.config.agent_schema import AgentConfig
from synthorg.config.provider_schema import ProviderConfig
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_MODEL_REFRESH_CYCLE_FAILED,
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

_PENDING_SCAN_PAGE_SIZE: int = 1_000


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

    @model_validator(mode="after")
    def _applied_within_recommended(self) -> Self:
        """Enforce that auto-applied never exceeds recommended.

        Returns:
            The validated report.

        Raises:
            ValueError: If ``auto_applied_count`` exceeds
                ``recommended_count`` (a counting bug).
        """
        if self.auto_applied_count > self.recommended_count:
            msg = "auto_applied_count cannot exceed recommended_count"
            raise ValueError(msg)
        return self


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

        setup = await self._load_cycle_inputs()
        if setup is None:
            return RefreshCycleReport()
        providers, seen_pending, agents = setup

        added = stale = recommended = auto_applied = 0
        scanned = 0
        for provider_name, provider in providers.items():
            scanned += 1
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
                except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                    reraise_critical(exc)
                    logger.warning(
                        PROVIDER_MODEL_REFRESH_PROVIDER_FAILED,
                        provider=provider_name,
                        note="recommendation_persist_failed",
                        current_model=rec.current_model_id,
                        recommended_model=rec.recommended_model_id,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
                    continue
                recommended += 1
                if auto_apply and apply_recommendation is not None:
                    try:
                        await apply_recommendation(stored)
                    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                        reraise_critical(exc)
                        logger.warning(
                            PROVIDER_MODEL_REFRESH_PROVIDER_FAILED,
                            provider=provider_name,
                            note="recommendation_auto_apply_failed",
                            rec_id=str(stored.id),
                            error_type=type(exc).__name__,
                            error=safe_error_description(exc),
                        )
                        continue
                    auto_applied += 1

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

    async def _load_cycle_inputs(
        self,
    ) -> (
        tuple[
            Mapping[str, ProviderConfig],
            set[tuple[str, str, str]],
            tuple[AgentConfig, ...],
        ]
        | None
    ):
        """Fetch the three independent cycle inputs concurrently.

        The provider catalogue, the pending-recommendation dedup set, and
        the agent roster are independent reads, so they run in a
        ``TaskGroup``.  A failure in any read aborts the cycle cleanly
        (logged with ``phase="setup"``) rather than surfacing as an
        unattributed per-provider failure.

        Returns:
            ``(providers, seen_pending, agents)`` on success, or ``None``
            when a setup read failed (the caller returns an empty report).
        """
        try:
            async with asyncio.TaskGroup() as tg:
                providers_task = tg.create_task(self._mgmt.list_providers())
                pending_task = tg.create_task(self._existing_pending_keys())
                agents_task = tg.create_task(self._config_resolver.get_agents())
            return (
                providers_task.result(),
                pending_task.result(),
                agents_task.result(),
            )
        except* Exception as eg:  # noqa: BLE001 -- criticals re-raised below
            for exc in eg.exceptions:
                reraise_critical(exc)
            first = eg.exceptions[0]
            logger.warning(
                PROVIDER_MODEL_REFRESH_CYCLE_FAILED,
                phase="setup",
                error_type=type(first).__name__,
                error=safe_error_description(first),
            )
        return None

    async def _existing_pending_keys(self) -> set[tuple[str, str, str]]:
        """Return keys of pending recommendations to dedup against.

        Pages through the full pending set so dedup stays correct beyond a
        single page; an artificial row cap would silently drop keys and
        let already-pending recommendations be re-created every cycle.

        Returns:
            A set of ``(provider, current_id, recommended_id)`` for every
            currently-pending recommendation.
        """
        keys: set[tuple[str, str, str]] = set()
        offset = 0
        while True:
            page = await self._repo.query(
                UpgradeRecommendationFilterSpec(status=RecommendationStatus.PENDING),
                limit=_PENDING_SCAN_PAGE_SIZE,
                offset=offset,
            )
            keys.update(
                (
                    row.recommendation.provider_name,
                    row.recommendation.current_model_id,
                    row.recommendation.recommended_model_id,
                )
                for row in page
            )
            if len(page) < _PENDING_SCAN_PAGE_SIZE:
                break
            offset += _PENDING_SCAN_PAGE_SIZE
        return keys

    async def _persist(
        self,
        rec: UpgradeRecommendation,
        agents: tuple[AgentConfig, ...],
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
    agents: tuple[AgentConfig, ...],
    rec: UpgradeRecommendation,
) -> tuple[str, ...]:
    """Return the names of agents pinned to the recommendation's current model.

    Returns:
        Agent names whose configured model matches ``rec``'s provider +
        current model id.
    """
    names: list[str] = []
    for agent in agents:
        model = agent.model
        if (
            model.get("provider") == rec.provider_name
            and model.get("model_id") == rec.current_model_id
        ):
            names.append(agent.name)
    return tuple(names)


__all__ = ["ApplyHook", "ModelRefreshService", "RefreshCycleReport"]
