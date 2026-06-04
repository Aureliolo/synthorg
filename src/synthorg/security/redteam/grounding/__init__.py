"""Grounding subsystem for the red-team gate.

The grounding subsystem decides whether assertive claims in a
deliverable trace to a source. The current ``HeuristicGroundingChecker``
implementation is deterministic and runs without an LLM or knowledge
store, so the gate can honestly catch ungrounded claims today.

The substrate-backed :class:`KnowledgeSubstrateGroundingChecker` plugs
into the same :class:`GroundingChecker` protocol via
:mod:`synthorg.security.redteam.grounding.factory`, behind the
``RedTeamConfig.grounding_checker_kind`` discriminator. Call sites in
:mod:`synthorg.security.redteam.gate` consume the protocol, so the swap
is local to the factory module.
"""

from synthorg.security.redteam.grounding.factory import (
    GroundingCheckerKind,
    build_grounding_checker,
)
from synthorg.security.redteam.grounding.heuristic import HeuristicGroundingChecker
from synthorg.security.redteam.grounding.models import UngroundedClaim
from synthorg.security.redteam.grounding.protocol import GroundingChecker
from synthorg.security.redteam.grounding.resolver import (
    GroundingSubstrateContext,
    GroundingSubstrateResolver,
)
from synthorg.security.redteam.grounding.substrate import (
    KnowledgeSubstrateGroundingChecker,
)

__all__ = [
    "GroundingChecker",
    "GroundingCheckerKind",
    "GroundingSubstrateContext",
    "GroundingSubstrateResolver",
    "HeuristicGroundingChecker",
    "KnowledgeSubstrateGroundingChecker",
    "UngroundedClaim",
    "build_grounding_checker",
]
