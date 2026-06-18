# module-kind: service
"""Application service for the upgrade-recommendation review surface.

Owns the recommendation lifecycle the dashboard + auto-apply flow drive:
list / get / approve / reject, plus the ``apply_auto`` hook the refresh
scheduler invokes for in-family auto-apply. Approving (or auto-applying)
reassigns every pinned agent to the recommended model through the
canonical agent-mutation path, so the same catalog-validated reassignment
runs whether a human or the auto-apply flow acted.
"""

from uuid import UUID

from synthorg.api.services.org_mutations import OrgMutationService
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_RESOURCE_CONFLICT,
    API_RESOURCE_NOT_FOUND,
)
from synthorg.observability.events.provider import (
    PROVIDER_MODEL_UPGRADE_APPROVED,
    PROVIDER_MODEL_UPGRADE_AUTO_APPLIED,
    PROVIDER_MODEL_UPGRADE_REASSIGN_FAILED,
    PROVIDER_MODEL_UPGRADE_REJECTED,
)
from synthorg.organization.models import UpdateAgentOrgRequest
from synthorg.persistence.upgrade_recommendation_protocol import (
    UpgradeRecommendationFilterSpec,
    UpgradeRecommendationRepository,
)
from synthorg.providers.enums import RecommendationStatus
from synthorg.providers.errors import (
    UpgradeRecommendationAlreadyDecidedError,
    UpgradeRecommendationNotFoundError,
)
from synthorg.providers.management.upgrade_models import StoredUpgradeRecommendation

logger = get_logger(__name__)

_AUTO_APPLY_ACTOR: str = "auto-apply"
_DEFAULT_PAGE_LIMIT: int = 100


class UpgradeRecommendationService:
    """Lifecycle + apply orchestration for upgrade recommendations."""

    def __init__(
        self,
        *,
        repo: UpgradeRecommendationRepository,
        org_mutations: OrgMutationService,
        clock: Clock | None = None,
    ) -> None:
        """Initialise the service.

        Args:
            repo: Durable recommendation store.
            org_mutations: Org agent-mutation service for reassignment.
            clock: Clock seam; defaults to ``SystemClock``.
        """
        self._repo = repo
        self._org_mutations = org_mutations
        self._clock = clock or SystemClock()

    async def list_recommendations(
        self,
        *,
        status: RecommendationStatus | None = None,
        limit: int = _DEFAULT_PAGE_LIMIT,
        offset: int = 0,
    ) -> tuple[StoredUpgradeRecommendation, ...]:
        """List recommendations, optionally filtered by status.

        Returns:
            The matching recommendations, newest-first.
        """
        return await self._repo.query(
            UpgradeRecommendationFilterSpec(status=status),
            limit=limit,
            offset=offset,
        )

    async def get_or_404(self, rec_id: UUID) -> StoredUpgradeRecommendation:
        """Return a recommendation or raise 404.

        Returns:
            The stored recommendation.

        Raises:
            UpgradeRecommendationNotFoundError: When absent.
        """
        found = await self._repo.get(rec_id)
        if found is None:
            msg = f"Upgrade recommendation {rec_id} not found"
            logger.warning(API_RESOURCE_NOT_FOUND, reason=msg, rec_id=str(rec_id))
            raise UpgradeRecommendationNotFoundError(msg)
        return found

    async def approve(
        self,
        rec_id: UUID,
        *,
        decided_by: str,
    ) -> StoredUpgradeRecommendation:
        """Approve a pending recommendation and reassign pinned agents.

        Returns:
            The updated (approved) recommendation.

        Raises:
            UpgradeRecommendationNotFoundError: When absent.
            UpgradeRecommendationAlreadyDecidedError: When not pending.
        """
        stored = await self.get_or_404(rec_id)
        await self._decide(
            stored,
            to_state=RecommendationStatus.APPROVED,
            decided_by=decided_by,
        )
        logger.info(
            PROVIDER_MODEL_UPGRADE_APPROVED,
            rec_id=str(rec_id),
            decided_by=decided_by,
            agents=len(stored.agent_ids),
        )
        await self._reassign(stored)
        return await self.get_or_404(rec_id)

    async def reject(
        self,
        rec_id: UUID,
        *,
        decided_by: str,
    ) -> StoredUpgradeRecommendation:
        """Reject a pending recommendation (no reassignment).

        Returns:
            The updated (rejected) recommendation.

        Raises:
            UpgradeRecommendationNotFoundError: When absent.
            UpgradeRecommendationAlreadyDecidedError: When not pending.
        """
        stored = await self.get_or_404(rec_id)
        await self._decide(
            stored,
            to_state=RecommendationStatus.REJECTED,
            decided_by=decided_by,
        )
        logger.info(
            PROVIDER_MODEL_UPGRADE_REJECTED,
            rec_id=str(rec_id),
            decided_by=decided_by,
        )
        return await self.get_or_404(rec_id)

    async def apply_auto(self, stored: StoredUpgradeRecommendation) -> None:
        """Auto-apply an in-family recommendation (the scheduler hook).

        Transitions ``PENDING -> AUTO_APPLIED`` and reassigns pinned
        agents. A lost CAS (already decided) is a no-op.
        """
        moved = await self._repo.transition_if(
            stored.id,
            from_state=RecommendationStatus.PENDING,
            to_state=RecommendationStatus.AUTO_APPLIED,
            decided_at=self._clock.now(),
            decided_by=_AUTO_APPLY_ACTOR,
        )
        if not moved:
            return
        await self._reassign(stored)
        logger.info(
            PROVIDER_MODEL_UPGRADE_AUTO_APPLIED,
            provider=stored.recommendation.provider_name,
            current_model=stored.recommendation.current_model_id,
            recommended_model=stored.recommendation.recommended_model_id,
            agents=len(stored.agent_ids),
        )

    async def _decide(
        self,
        stored: StoredUpgradeRecommendation,
        *,
        to_state: RecommendationStatus,
        decided_by: str,
    ) -> None:
        """Atomically move a pending recommendation to a decided state.

        Raises:
            UpgradeRecommendationAlreadyDecidedError: When the CAS loses
                (the recommendation was not pending).
        """
        moved = await self._repo.transition_if(
            stored.id,
            from_state=RecommendationStatus.PENDING,
            to_state=to_state,
            decided_at=self._clock.now(),
            decided_by=decided_by,
        )
        if not moved:
            msg = f"Recommendation {stored.id} is not pending"
            logger.warning(API_RESOURCE_CONFLICT, reason=msg, rec_id=str(stored.id))
            raise UpgradeRecommendationAlreadyDecidedError(msg)

    async def _reassign(self, stored: StoredUpgradeRecommendation) -> None:
        """Re-point each pinned agent at the recommended model.

        Best-effort per agent: a stale agent id (renamed / deleted since
        the recommendation was produced) is logged and skipped so one
        missing agent never fails the whole apply.
        """
        rec = stored.recommendation
        for agent_name in stored.agent_ids:
            try:
                await self._org_mutations.update_agent(
                    agent_name,
                    UpdateAgentOrgRequest(
                        model_provider=rec.provider_name,
                        model_id=rec.recommended_model_id,
                    ),
                )
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    PROVIDER_MODEL_UPGRADE_REASSIGN_FAILED,
                    agent=agent_name,
                    provider=rec.provider_name,
                    recommended_model=rec.recommended_model_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )


__all__ = ["UpgradeRecommendationService"]
