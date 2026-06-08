"""IdentityAdapter -- applies identity mutations via IdentityVersionStore."""

from synthorg.core.types import NotBlankStr
from synthorg.engine.evolution.models import (
    AdaptationAxis,
    AdaptationProposal,
)
from synthorg.engine.identity.store.protocol import IdentityVersionStore
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.evolution import EVOLUTION_ADAPTATION_FAILED

logger = get_logger(__name__)


class IdentityAdapter:
    """Applies identity changes via versioned storage.

    Retrieves the current agent identity, applies the proposal changes
    via model_copy(), and persists the evolved identity through the
    IdentityVersionStore.
    """

    def __init__(self, identity_store: IdentityVersionStore) -> None:
        """Initialize IdentityAdapter.

        Args:
            identity_store: Versioned identity storage backend.
        """
        self._identity_store = identity_store

    @property
    def name(self) -> str:
        """Return adapter name."""
        return "IdentityAdapter"

    @property
    def axis(self) -> AdaptationAxis:
        """Return the adaptation axis this adapter handles."""
        return AdaptationAxis.IDENTITY

    async def apply(
        self,
        proposal: AdaptationProposal,
        agent_id: NotBlankStr,
    ) -> None:
        """Apply the approved identity adaptation.

        Retrieves the current identity, applies changes via model_copy(),
        and persists the new version.

        Args:
            proposal: The approved proposal to apply.
            agent_id: Target agent.

        Raises:
            ValueError: When the proposal targets an unknown axis or
                the requested changes cannot be applied to the
                current identity (e.g. invalid field path).
            Exception: If the identity cannot be retrieved or persisted.
        """
        try:
            current_identity = await self._identity_store.get_current(agent_id)
            if current_identity is None:
                msg = f"Agent {agent_id} not found in identity store"
                raise ValueError(msg)  # noqa: TRY301

            evolved_identity = current_identity.model_copy(
                update=proposal.changes,
            )

            await self._identity_store.put(
                agent_id,
                evolved_identity,
                saved_by="evolution",
            )
        except Exception as exc:
            logger.warning(
                EVOLUTION_ADAPTATION_FAILED,
                agent_id=agent_id,
                proposal_id=str(proposal.id),
                axis=proposal.axis.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
