"""Factory for the configured :class:`GroundingChecker`.

The factory is the swap point between the deterministic heuristic checker
and the substrate-backed checker. The discriminator lives on
:class:`synthorg.security.config.RedTeamConfig`; the gate's call sites
(:mod:`synthorg.security.redteam.gate`) do not change between the two.

The substrate-backed checker needs a lazy
:data:`GroundingSubstrateResolver` because it is built at boot before the
knowledge substrate finishes wiring. When the discriminator selects the
substrate checker but no resolver was threaded (a persistence-less or
provider-less boot), the factory degrades to the heuristic rather than
failing the boot.
"""

from typing import Literal

from synthorg.observability import get_logger
from synthorg.observability.events.red_team import (
    RED_TEAM_GROUNDING_SUBSTRATE_DEGRADED,
)
from synthorg.security.redteam.grounding.heuristic import HeuristicGroundingChecker
from synthorg.security.redteam.grounding.protocol import GroundingChecker
from synthorg.security.redteam.grounding.resolver import (
    GroundingSubstrateResolver,
)

logger = get_logger(__name__)

GroundingCheckerKind = Literal["heuristic", "knowledge_substrate"]
"""Allowed values for the ``RedTeamConfig.grounding_checker_kind`` field.

``heuristic`` is the safe default (deterministic regex, LOW-capped).
``knowledge_substrate`` selects the LLM claim-extraction + semantic
entailment checker, which escalates to HIGH on the GROUNDING surface.
"""


def build_grounding_checker(
    kind: GroundingCheckerKind,
    *,
    substrate_resolver: GroundingSubstrateResolver | None = None,
) -> GroundingChecker:
    """Return the configured grounding checker.

    Args:
        kind: Discriminator from :class:`RedTeamConfig`.
        substrate_resolver: Lazy resolver for the knowledge service +
            provider, required to build the substrate checker. When
            ``kind`` is ``"knowledge_substrate"`` but this is ``None``,
            the factory degrades to the heuristic.

    Returns:
        A concrete :class:`GroundingChecker`.

    Raises:
        ValueError: If ``kind`` is not a recognised discriminator.
    """
    if kind == "heuristic":
        return HeuristicGroundingChecker()
    if kind == "knowledge_substrate":
        if substrate_resolver is None:
            logger.warning(
                RED_TEAM_GROUNDING_SUBSTRATE_DEGRADED,
                reason="no_resolver_at_build",
                note="grounding_checker_kind=knowledge_substrate but no "
                "substrate resolver was threaded; using heuristic checker",
            )
            return HeuristicGroundingChecker()
        from synthorg.security.redteam.grounding.substrate import (  # noqa: PLC0415
            KnowledgeSubstrateGroundingChecker,
        )

        return KnowledgeSubstrateGroundingChecker(resolver=substrate_resolver)
    # Runtime guard: untyped config payloads (YAML / env) reach this
    # branch even though the Literal narrows it away at type-check time.
    msg = f"Unknown grounding checker kind: {kind!r}"  # type: ignore[unreachable]
    raise ValueError(msg)
