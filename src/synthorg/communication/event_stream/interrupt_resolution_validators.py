"""Per-`InterruptType` resolution-payload validators.

Each :class:`~synthorg.communication.event_stream.interrupt.InterruptType`
member maps to a callable that inspects an
:class:`~synthorg.communication.event_stream.interrupt.InterruptResolution`
and returns ``None`` when the payload is valid or a short note string
that the dispatcher logs as ``EVENT_STREAM_INVALID_RESUME_PAYLOAD``'s
``note=`` field on rejection.

Returning a string instead of raising keeps the calling
:meth:`InterruptStore.resolve` flow flat: the caller logs once and
returns ``None`` to its own caller without unwinding through an
exception layer for every rejection.
"""

from collections.abc import Callable
from typing import Final

from synthorg.communication.event_stream.interrupt import (
    InterruptResolution,
    InterruptType,
)
from synthorg.core.registry import StrategyRegistry

# ``None`` means the resolution payload satisfies the interrupt's contract;
# a ``str`` is the short note the dispatcher passes verbatim as ``note=`` on
# the ``EVENT_STREAM_INVALID_RESUME_PAYLOAD`` warning. Naming the alias makes
# that convention type-checkable instead of docstring-only.
type ResolutionValidationResult = str | None
type ResolutionValidator = Callable[[InterruptResolution], ResolutionValidationResult]


def _validate_tool_approval(
    resolution: InterruptResolution,
) -> ResolutionValidationResult:
    """Validate a TOOL_APPROVAL resolution.

    Returns:
        A rejection note if ``decision`` is missing, else ``None``.
    """
    if resolution.decision is None:
        return "TOOL_APPROVAL requires decision"
    return None


def _validate_info_request(
    resolution: InterruptResolution,
) -> ResolutionValidationResult:
    """Validate an INFO_REQUEST resolution.

    Returns:
        A rejection note if ``response`` is missing, else ``None``.
    """
    if resolution.response is None:
        return "INFO_REQUEST requires response"
    return None


# Keyed by ``InterruptType`` (a ``StrEnum``); the registry normalises
# members to their string value so a lookup by either the enum member
# or the raw value resolves the same validator. An unregistered
# interrupt type raises ``StrategyFactoryNotFoundError`` at lookup,
# which :meth:`InterruptStore.resolve` maps to a rejection note.
INTERRUPT_RESOLUTION_VALIDATOR_REGISTRY: Final[
    StrategyRegistry[ResolutionValidationResult]
] = StrategyRegistry(
    {
        InterruptType.TOOL_APPROVAL: _validate_tool_approval,
        InterruptType.INFO_REQUEST: _validate_info_request,
    },
    kind="interrupt_resolution_validator",
)
