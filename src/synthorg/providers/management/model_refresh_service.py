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
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.config.agent_schema import AgentConfig
from synthorg.config.provider_schema import ProviderConfig
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_MODEL_REFRESH_CYCLE_FAILED,
    PROVIDER_MODEL_REFRESH_CYCLE_RAN,
    PROVIDER_MODEL_REFRESH_PROVIDER_FAILED,
    PROVIDER_MODEL_UPGRADE_RECOMMENDED,
    PROVIDER_MODEL_UPGRADE_SUPERSEDED,
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

_RecKey = tuple[str, str, str]
"""``(provider, current_model_id, recommended_model_id)`` recommendation key."""

_PENDING_SCAN_PAGE_SIZE: int = 1_000
_RECONCILE_ACTOR: str = "reconcile"
"""System principal stamped on a recommendation retired by a reconcile pass."""


class RefreshCycleReport(BaseModel):
    """Aggregate outcome of one refresh cycle across all providers.

    Attributes:
        providers_scanned: Number of providers reconciled.
        added_count: Newly-discovered models persisted.
        stale_count: Configured ids flagged stale.
        recommended_count: New recommendations persisted this cycle.
        auto_applied_count: Recommendations auto-applied this cycle.
        superseded_count: Pending recommendations retired this cycle because
            the recommender no longer produces them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    providers_scanned: int = Field(default=0, ge=0)
    added_count: int = Field(default=0, ge=0)
    stale_count: int = Field(default=0, ge=0)
    recommended_count: int = Field(default=0, ge=0)
    auto_applied_count: int = Field(default=0, ge=0)
    superseded_count: int = Field(default=0, ge=0)

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
        notification_dispatcher: NotificationDispatcher | None = None,
        clock: Clock | None = None,
    ) -> None:
        """Initialise the service.

        Args:
            mgmt_service: Provider management service (catalog + mutations).
            probe: Live-discovery presence probe.
            recommender: In-family upgrade recommender.
            repo: Durable recommendation store.
            config_resolver: Reads provider + agent config.
            notification_dispatcher: Operator alert sink for stale
                (no-longer-served) configured models; ``None`` disables alerting.
            clock: Clock seam; defaults to ``SystemClock``.
        """
        self._mgmt = mgmt_service
        self._probe = probe
        self._recommender = recommender
        self._repo = repo
        self._config_resolver = config_resolver
        self._notification_dispatcher = notification_dispatcher
        self._clock = clock or SystemClock()
        # (provider, model) tuples already alerted as stale, so a stale model
        # that persists across reconcile cycles raises a single actionable
        # alert instead of re-notifying the operator every cycle.
        self._alerted_stale: set[tuple[str, str]] = set()

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
        providers, pending_rows, agents = setup
        # A pending row's key can only be re-created once, so dedup against a
        # mutable set seeded from the snapshot; ``pending_rows`` keeps the ids
        # for the retirement pass.
        seen_pending: set[_RecKey] = {key for key, _ in pending_rows}

        added = stale = recommended = auto_applied = superseded = 0
        scanned = 0
        stale_by_provider: list[tuple[str, tuple[str, ...]]] = []
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
            if outcome.stale_ids:
                stale_by_provider.append((provider_name, tuple(outcome.stale_ids)))
            # Only recommend upgrades for a model an agent actually runs: a rec
            # whose current model has no pinned agent reassigns nobody on
            # approve, so the whole catalogue of unused models would otherwise
            # surface as no-op recommendations. Gating on pinned agents also
            # drives retirement below -- an existing pending rec whose model
            # fell out of use is no longer "produced" and gets superseded.
            usable = [
                (rec, ids)
                for rec in outcome.recommendations
                if (ids := _pinned_agent_ids(agents, rec))
            ]
            for rec, agent_ids in usable:
                key = (
                    rec.provider_name,
                    rec.current_model_id,
                    rec.recommended_model_id,
                )
                if key in seen_pending:
                    continue
                seen_pending.add(key)
                try:
                    stored = await self._persist(rec, agent_ids)
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

            # Retire this provider's pending rows the recommender no longer
            # produces (current model removed, or a changed newest-in-family
            # pick). Guarded on the recommend step having actually run and
            # succeeded: a failed step or a detect-only pass yields an empty
            # set that must NOT be read as "retire everything".
            reconciling = mode is RefreshMode.RECONCILE_RECOMMEND
            if reconciling and outcome.recommendations_valid:
                superseded += await self._retire_obsolete(
                    provider_name,
                    tuple(rec for rec, _ in usable),
                    pending_rows,
                )

        await self._alert_stale_models(stale_by_provider)
        report = RefreshCycleReport(
            providers_scanned=scanned,
            added_count=added,
            stale_count=stale,
            recommended_count=recommended,
            auto_applied_count=auto_applied,
            superseded_count=superseded,
        )
        logger.info(
            PROVIDER_MODEL_REFRESH_CYCLE_RAN,
            providers_scanned=scanned,
            added_count=added,
            stale_count=stale,
            recommended_count=recommended,
            auto_applied_count=auto_applied,
            superseded_count=superseded,
        )
        return report

    async def _retire_obsolete(
        self,
        provider_name: str,
        recommendations: tuple[UpgradeRecommendation, ...],
        pending_rows: tuple[tuple[_RecKey, UUID], ...],
    ) -> int:
        """Supersede *provider_name*'s pending rows no longer produced.

        *recommendations* is the authoritative current set the recommender
        produced for the provider this cycle; any pending row for the same
        provider whose key is absent is obsolete and retired. Rows for other
        providers are skipped (each provider is reconciled independently).

        Returns:
            The number of pending recommendations retired.
        """
        produced = {
            (r.provider_name, r.current_model_id, r.recommended_model_id)
            for r in recommendations
        }
        retired = 0
        for key, rec_id in pending_rows:
            if key[0] != provider_name or key in produced:
                continue
            if await self._supersede(rec_id):
                retired += 1
        return retired

    async def _supersede(self, rec_id: UUID) -> bool:
        """Transition a pending recommendation to ``SUPERSEDED``.

        A lost CAS (already decided by a human, or already retired) is a
        no-op, so a concurrent approve/reject is never clobbered.

        Returns:
            ``True`` iff this call moved the row ``PENDING -> SUPERSEDED``.
        """
        moved = await self._repo.transition_if(
            rec_id,
            from_state=RecommendationStatus.PENDING,
            to_state=RecommendationStatus.SUPERSEDED,
            decided_at=self._clock.now(),
            decided_by=_RECONCILE_ACTOR,
        )
        if moved:
            logger.info(PROVIDER_MODEL_UPGRADE_SUPERSEDED, rec_id=str(rec_id))
        return moved

    async def _alert_stale_models(
        self, stale_by_provider: list[tuple[str, tuple[str, ...]]]
    ) -> None:
        """Alert the operator that configured models are no longer served.

        Best-effort: a missing dispatcher or a sink failure never breaks the
        refresh cycle (criticals re-raise).

        Args:
            stale_by_provider: ``(provider_name, stale_model_ids)`` pairs found
                this cycle.
        """
        if self._notification_dispatcher is None:
            return
        # Drop healed entries from the suppression set first, so a model that
        # recovers (leaves the stale set) and later goes stale again alerts
        # afresh instead of staying permanently muted.
        current_stale = {(name, mid) for name, ids in stale_by_provider for mid in ids}
        self._alerted_stale &= current_stale
        # Only alert on stale (provider, model) tuples not already reported, so
        # a stale model persisting across cycles does not re-notify every pass.
        new_by_provider = [
            (name, tuple(mid for mid in ids if (name, mid) not in self._alerted_stale))
            for name, ids in stale_by_provider
        ]
        new_by_provider = [(name, ids) for name, ids in new_by_provider if ids]
        if not new_by_provider:
            return
        from synthorg.notifications.models import (  # noqa: PLC0415
            Notification,
            NotificationCategory,
            NotificationSeverity,
        )

        lines = [f"{name}: {', '.join(ids)}" for name, ids in new_by_provider]
        body = (
            "Configured models are no longer served by their provider and "
            "should be replaced (see model recommendations):\n" + "\n".join(lines)
        )
        try:
            await self._notification_dispatcher.dispatch(
                Notification(
                    category=NotificationCategory.HEALTH,
                    severity=NotificationSeverity.WARNING,
                    title="Configured models no longer served",
                    body=body,
                    source="providers.model_refresh",
                ),
            )
            for name, ids in new_by_provider:
                self._alerted_stale.update((name, mid) for mid in ids)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                PROVIDER_MODEL_REFRESH_CYCLE_FAILED,
                note="stale_model_alert_dispatch_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def _load_cycle_inputs(
        self,
    ) -> (
        tuple[
            Mapping[str, ProviderConfig],
            tuple[tuple[_RecKey, UUID], ...],
            tuple[AgentConfig, ...],
        ]
        | None
    ):
        """Fetch the three independent cycle inputs concurrently.

        The provider catalogue, the pending recommendations (keys + ids), and
        the agent roster are independent reads, so they run in a
        ``TaskGroup``.  A failure in any read aborts the cycle cleanly
        (logged with ``phase="setup"``) rather than surfacing as an
        unattributed per-provider failure.

        Returns:
            ``(providers, pending_rows, agents)`` on success, or ``None``
            when a setup read failed (the caller returns an empty report).
        """
        try:
            async with asyncio.TaskGroup() as tg:
                providers_task = tg.create_task(self._mgmt.list_providers())
                pending_task = tg.create_task(self._existing_pending())
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

    async def _existing_pending(self) -> tuple[tuple[_RecKey, UUID], ...]:
        """Return every pending recommendation's key + id.

        Pages through the full pending set so both dedup and retirement stay
        correct beyond a single page; an artificial row cap would silently
        drop keys, re-creating already-pending recommendations and leaving
        obsolete ones unretired. Rows are returned as ``(key, id)`` pairs
        (not a key set) so a retiring pass can transition each row by id, and
        duplicate-key rows are each retired rather than collapsed.

        Returns:
            A tuple of ``((provider, current_id, recommended_id), id)`` for
            every currently-pending recommendation.
        """
        spec = UpgradeRecommendationFilterSpec(status=RecommendationStatus.PENDING)
        rows: list[tuple[_RecKey, UUID]] = []
        offset = 0
        page = await self._repo.query(
            spec, limit=_PENDING_SCAN_PAGE_SIZE, offset=offset
        )
        while page:
            rows.extend(
                (
                    (
                        row.recommendation.provider_name,
                        row.recommendation.current_model_id,
                        row.recommendation.recommended_model_id,
                    ),
                    row.id,
                )
                for row in page
            )
            if len(page) < _PENDING_SCAN_PAGE_SIZE:
                break
            offset += _PENDING_SCAN_PAGE_SIZE
            page = await self._repo.query(
                spec, limit=_PENDING_SCAN_PAGE_SIZE, offset=offset
            )
        return tuple(rows)

    async def _persist(
        self,
        rec: UpgradeRecommendation,
        agent_ids: tuple[str, ...],
    ) -> StoredUpgradeRecommendation:
        """Persist *rec* as a pending recommendation with its pinned agents.

        *agent_ids* is the non-empty set of agents pinned to *rec*'s current
        model (the caller only persists usage-backed recommendations).

        Returns:
            The stored recommendation.
        """
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
