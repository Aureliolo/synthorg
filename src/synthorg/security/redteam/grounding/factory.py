"""Factory for the configured :class:`GroundingChecker`.

The factory is the swap point for EPIC E #1988: when the knowledge +
provenance substrate ships, this module gains a
``KnowledgeSubstrateGroundingChecker`` branch behind a discriminator
in :class:`synthorg.security.config.RedTeamConfig`. The gate's call
sites (:mod:`synthorg.security.redteam.gate`) do not change.
"""

from typing import TYPE_CHECKING, Literal

from synthorg.security.redteam.grounding.heuristic import HeuristicGroundingChecker

if TYPE_CHECKING:
    from synthorg.security.redteam.grounding.protocol import GroundingChecker

GroundingCheckerKind = Literal["heuristic"]
"""Allowed values for the ``RedTeamConfig.grounding_checker_kind`` field.

#1988 will extend this with ``"knowledge_substrate"``; tests covering
the literal will need updating in lockstep with the substrate landing.
"""


def build_grounding_checker(kind: GroundingCheckerKind) -> GroundingChecker:
    """Return the configured grounding checker.

    Args:
        kind: Discriminator from :class:`RedTeamConfig`. Only
            ``"heuristic"`` is supported today.

    Returns:
        A concrete :class:`GroundingChecker`.

    Raises:
        ValueError: If ``kind`` is not a recognised discriminator.
    """
    if kind == "heuristic":
        return HeuristicGroundingChecker()
    msg = f"Unknown grounding checker kind: {kind!r}"
    raise ValueError(msg)
