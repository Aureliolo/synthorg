# module-kind: code
"""Model-id to capability-archetype resolution for the cost-dial.

The Pareto downgrade traversal and the measured benchmark provider both
need to map a model id onto one of the canonical rungs
(:data:`~synthorg.core.types.CapabilityLevel`). The default heuristic
recognises the vendor-agnostic ``example-<level>-<rev>`` archetype ids; an
operator running arbitrary model ids supplies a
:class:`ModelCapabilityMap` of explicit overrides so those ids also resolve
(additive: the heuristic still applies to anything the map does not name).

Locality is a second, independent axis. A model an operator hosts costs
almost nothing per turn whatever it can do, so :func:`heuristic_is_local`
answers that separately rather than the ladder carrying a rung that is
really two claims at once.
"""

import re
from collections.abc import Mapping
from typing import Final, cast

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import CapabilityLevel, NotBlankStr

#: A documented ``example-<level>-<rev>`` archetype id, optionally carrying a
#: ``local`` locality segment. Anchored on the ``example-`` prefix so only the
#: vendor-agnostic sample ids resolve a rung; arbitrary operator ids fall
#: through to ``None`` (and an explicit :class:`ModelCapabilityMap` override)
#: rather than being silently classified.
_EXAMPLE_LEVEL_RE: Final[re.Pattern[str]] = re.compile(
    r"^example-(?:local-)?(basic|capable|expert)(?:-.+)?$"
)
#: A ``local`` segment in the archetype's locality position. Position
#: matters: an unrelated id merely containing ``local`` says nothing about
#: where the model runs.
_LOCAL_RE: Final[re.Pattern[str]] = re.compile(r"^example-local-")


def heuristic_capability(model_id: str) -> CapabilityLevel | None:
    """Map a vendor-agnostic ``example-<level>-<rev>`` id to its rung.

    Matching is restricted to the documented archetype shape, so an
    unrelated id is not silently classified; such ids resolve a rung only
    through an explicit :class:`ModelCapabilityMap` override.

    Returns:
        The rung (``basic`` / ``capable`` / ``expert``), or ``None`` when
        the id matches no known archetype.
    """
    match = _EXAMPLE_LEVEL_RE.match(model_id.lower())
    if match is None:
        return None
    # The capture group is one of the three rungs by construction of the
    # pattern, which is the only thing that keeps this narrowing honest.
    return cast("CapabilityLevel", match.group(1))


def heuristic_is_local(model_id: str) -> bool:
    """Report whether an archetype id names a locally-hosted model.

    Returns:
        ``True`` for a documented ``example-local-<level>-<rev>`` id.
    """
    return _LOCAL_RE.match(model_id.lower()) is not None


class ModelCapabilityMap(BaseModel):
    """Operator-configured ``model_id`` to capability-rung overrides.

    An empty map (the default) leaves resolution entirely to
    :func:`heuristic_capability`, so a normal boot is unchanged. Values are
    typed against the canonical rungs, so a non-canonical one is rejected
    at construction rather than at wiring.

    Attributes:
        overrides: Explicit ``model_id`` to rung mappings consulted before
            the heuristic.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    overrides: Mapping[NotBlankStr, CapabilityLevel] = Field(default_factory=dict)


def resolve_capability(
    model_id: str,
    capability_map: ModelCapabilityMap | None = None,
) -> CapabilityLevel | None:
    """Resolve a model id to a rung, overrides first then the heuristic.

    Returns:
        The rung, or ``None`` when neither the override map nor the
        heuristic recognises the id.
    """
    if capability_map is not None:
        override = capability_map.overrides.get(NotBlankStr(model_id))
        if override is not None:
            return override
    return heuristic_capability(model_id)


__all__ = [
    "ModelCapabilityMap",
    "heuristic_capability",
    "heuristic_is_local",
    "resolve_capability",
]
