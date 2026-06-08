"""Trust-driven narrowing of an agent's tool permissions.

The progressive-trust system lets an agent *earn* higher tool access
over time. Enforcement is the inverse: until an agent has earned a
level, its effective tool access is the more restrictive of its
configured identity access level and its currently-held trust level.
This module is the single pure place that math lives so it can be
unit-tested in isolation and reused by the engine tool-invoker seam.
"""

from typing import TYPE_CHECKING, NamedTuple

from synthorg.security.trust.levels import TRUST_LEVEL_RANK

if TYPE_CHECKING:
    from synthorg.core.agent import ToolPermissions
    from synthorg.core.tool_constraints import ToolAccessLevel


class EffectiveToolPermissions(NamedTuple):
    """Outcome of trust-driven tool-permission narrowing.

    Named (not a bare tuple) so call sites cannot transpose the
    permissions and the observability flag.
    """

    permissions: ToolPermissions
    was_narrowed: bool


def resolve_effective_tool_permissions(
    identity_tools: ToolPermissions,
    trust_level: ToolAccessLevel,
) -> EffectiveToolPermissions:
    """Return the effective permissions after applying earned trust.

    Takes the more restrictive of the agent's identity access level and
    its currently-earned trust level.

    ``CUSTOM`` on either side is left untouched: a ``CUSTOM`` permission
    set is governed by explicit allow/deny lists, so category-level
    narrowing is orthogonal and would silently break it. When trust is
    at or above the identity level the identity permissions pass through
    unchanged -- trust never *grants* access beyond what the identity
    already allows; it only withholds until earned.

    Args:
        identity_tools: The agent's configured tool permissions.
        trust_level: The agent's current trust level.

    Returns:
        An :class:`EffectiveToolPermissions` of the effective
        permissions (a narrowed copy when trust is stricter, otherwise
        the input unchanged) and a flag noting whether narrowing was
        applied (for observability).
    """
    id_rank = TRUST_LEVEL_RANK.get(identity_tools.access_level)
    trust_rank = TRUST_LEVEL_RANK.get(trust_level)
    if id_rank is None or trust_rank is None or trust_rank >= id_rank:
        return EffectiveToolPermissions(identity_tools, was_narrowed=False)
    return EffectiveToolPermissions(
        identity_tools.model_copy(update={"access_level": trust_level}),
        was_narrowed=True,
    )
