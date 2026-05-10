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

from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Final

from synthorg.communication.event_stream.interrupt import (
    InterruptResolution,
    InterruptType,
)

type ResolutionValidator = Callable[[InterruptResolution], str | None]


def _validate_tool_approval(resolution: InterruptResolution) -> str | None:
    if resolution.decision is None:
        return "TOOL_APPROVAL requires decision"
    return None


def _validate_info_request(resolution: InterruptResolution) -> str | None:
    if resolution.response is None:
        return "INFO_REQUEST requires response"
    return None


INTERRUPT_RESOLUTION_VALIDATORS: Final[Mapping[InterruptType, ResolutionValidator]] = (
    MappingProxyType(
        {
            InterruptType.TOOL_APPROVAL: _validate_tool_approval,
            InterruptType.INFO_REQUEST: _validate_info_request,
        },
    )
)
