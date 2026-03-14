"""Seniority-based model mapping strategy.

Implements D15: model follows seniority level by default, using
the role catalog's ``typical_model_tier`` to resolve the appropriate
model tier for each seniority level.
"""

from typing import TYPE_CHECKING

from synthorg.core.role_catalog import get_seniority_info
from synthorg.observability import get_logger
from synthorg.observability.events.promotion import PROMOTION_MODEL_CHANGED

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.enums import SeniorityLevel
    from synthorg.hr.promotion.config import ModelMappingConfig

logger = get_logger(__name__)


class SeniorityModelMapping:
    """Model mapping strategy based on seniority level.

    When ``model_follows_seniority`` is enabled, resolves the model
    tier from the role catalog and returns the tier string as the
    new model identifier. Explicit overrides in ``seniority_model_map``
    take precedence.
    """

    def __init__(self, *, config: ModelMappingConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        """Strategy name identifier."""
        return "seniority_model_mapping"

    def resolve_model(
        self,
        *,
        agent_identity: AgentIdentity,
        new_level: SeniorityLevel,
    ) -> str | None:
        """Resolve the model for an agent at a new seniority level.

        Args:
            agent_identity: The agent's current identity.
            new_level: The new seniority level.

        Returns:
            New model_id string, or None if no change needed.
        """
        if not self._config.model_follows_seniority:
            return None

        current_model = str(agent_identity.model.model_id)

        level_key = new_level.value
        if level_key in self._config.seniority_model_map:
            new_model = str(self._config.seniority_model_map[level_key])
            if new_model == current_model:
                return None
            logger.info(
                PROMOTION_MODEL_CHANGED,
                agent_id=str(agent_identity.id),
                old_model=current_model,
                new_model=new_model,
                source="explicit_override",
            )
            return new_model

        # Use role catalog tier mapping
        seniority_info = get_seniority_info(new_level)
        new_tier = seniority_info.typical_model_tier

        if new_tier == current_model:
            return None

        logger.info(
            PROMOTION_MODEL_CHANGED,
            agent_id=str(agent_identity.id),
            old_model=current_model,
            new_model=new_tier,
            source="seniority_tier",
        )
        return new_tier
