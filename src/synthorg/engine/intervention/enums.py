"""Operator intervention domain enumerations."""

from enum import StrEnum


class InterventionKind(StrEnum):
    """Operator intervention applied from the mission-control cockpit.

    PAUSE and KILL reuse the task lifecycle seams (transition to
    ``INTERRUPTED`` / cancel to ``CANCELLED``). HINT and REDIRECT route
    through the steering directive: both post an ``INFO_REQUEST``
    interrupt the engine consumes at the next safe turn boundary, so the
    operator's text reaches the running agent without corrupting state.
    """

    PAUSE = "pause"
    KILL = "kill"
    HINT = "hint"
    REDIRECT = "redirect"
