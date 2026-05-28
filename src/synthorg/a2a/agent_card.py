"""Agent Card builder -- safe-subset projection of AgentIdentity.

Computes A2A Agent Cards from the current ``AgentIdentity`` at
request time.  The safe subset excludes sensitive internal
fields (personality, seniority, authority, model config, memory,
tool permissions, budget, autonomy, strategic mode, hiring date).
"""

from collections.abc import Sequence
from typing import Literal

from synthorg.a2a.models import (
    A2AAgentCard,
    A2AAgentProvider,
    A2AAgentSkill,
    A2AAuthSchemeInfo,
)
from synthorg.core.agent import (
    AgentIdentity,
)
from synthorg.observability import get_logger
from synthorg.observability.events.a2a import (
    A2A_AGENT_CARD_BUILT,
)

logger = get_logger(__name__)


def _identity_to_skills(identity: AgentIdentity) -> tuple[A2AAgentSkill, ...]:
    """Map an agent's SkillSet to A2A AgentSkill descriptors.

    Passes ``id``, ``name``, ``description``, ``input_modes``, and
    ``output_modes`` through verbatim; augments each skill's ``tags``
    tuple with a ``"primary"`` or ``"secondary"`` marker so external
    discovery can distinguish tiers.  Primary skills are emitted
    first, then secondary skills.

    Args:
        identity: Agent identity to extract skills from.

    Returns:
        Tuple of A2A agent skill descriptors.
    """
    primary = tuple(
        A2AAgentSkill(
            id=s.id,
            name=s.name,
            description=s.description,
            tags=_with_tier_marker(s.tags, "primary"),
            input_modes=s.input_modes,
            output_modes=s.output_modes,
        )
        for s in identity.skills.primary
    )
    secondary = tuple(
        A2AAgentSkill(
            id=s.id,
            name=s.name,
            description=s.description,
            tags=_with_tier_marker(s.tags, "secondary"),
            input_modes=s.input_modes,
            output_modes=s.output_modes,
        )
        for s in identity.skills.secondary
    )
    return primary + secondary


def _with_tier_marker(
    tags: tuple[str, ...],
    marker: Literal["primary", "secondary"],
) -> tuple[str, ...]:
    """Append the tier marker to tags, stripping any pre-existing tier tags.

    Ensures the emitted A2A ``tags`` tuple carries exactly one tier marker
    (``"primary"`` or ``"secondary"``) regardless of what the internal Skill
    stored, keeping the external contract unambiguous and duplicate-free.
    """
    cleaned = tuple(t for t in tags if t not in ("primary", "secondary"))
    return (*cleaned, marker)


class AgentCardBuilder:
    """Builds A2A Agent Cards from AgentIdentity instances.

    The builder is stateless -- each call computes a fresh card.
    Caching is handled by the caller (well-known controller).

    Args:
        default_auth_schemes: Auth schemes to advertise in every
            Agent Card.
    """

    __slots__ = ("_default_auth_schemes",)

    def __init__(
        self,
        default_auth_schemes: tuple[A2AAuthSchemeInfo, ...] = (),
    ) -> None:
        self._default_auth_schemes = default_auth_schemes

    def build(
        self,
        identity: AgentIdentity,
        base_url: str,
    ) -> A2AAgentCard:
        """Build an Agent Card for a single agent.

        Safe subset includes: name, role (as description),
        department, skills.  Everything else is excluded.

        Args:
            identity: The agent identity to project.
            base_url: Base URL where this agent's A2A endpoint
                lives.

        Returns:
            A2A Agent Card with safe-subset fields only.
        """
        card = A2AAgentCard(
            name=identity.name,
            description=f"{identity.role} in {identity.department}",
            url=base_url,
            skills=_identity_to_skills(identity),
            auth_schemes=self._default_auth_schemes,
        )
        logger.debug(
            A2A_AGENT_CARD_BUILT,
            agent_name=identity.name,
            skill_count=len(card.skills),
        )
        return card

    def build_company_card(
        self,
        identities: Sequence[AgentIdentity],
        base_url: str,
        company_name: str,
    ) -> A2AAgentCard:
        """Build an aggregated company-level Agent Card.

        Collects skills from all agents into a single card that
        represents the organization's combined capabilities.

        Args:
            identities: All agent identities to aggregate.
            base_url: Base URL for the company A2A endpoint.
            company_name: Organization name.

        Returns:
            Aggregated A2A Agent Card.
        """
        all_skills: list[A2AAgentSkill] = []
        seen_skill_ids: set[str] = set()
        for identity in identities:
            for skill in _identity_to_skills(identity):
                if skill.id not in seen_skill_ids:
                    all_skills.append(skill)
                    seen_skill_ids.add(skill.id)

        card = A2AAgentCard(
            name=company_name,
            description=f"{company_name} -- {len(identities)} agents",
            url=base_url,
            skills=tuple(all_skills),
            auth_schemes=self._default_auth_schemes,
            provider=A2AAgentProvider(
                organization=company_name,
            ),
        )
        logger.debug(
            A2A_AGENT_CARD_BUILT,
            agent_name=company_name,
            skill_count=len(card.skills),
            agent_count=len(identities),
        )
        return card
