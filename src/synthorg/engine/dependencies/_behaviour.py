# module-kind: declarative
"""Operator switches that change what an agent may do rather than what it has."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class EngineBehaviour:
    """Per-deployment behaviour flags read at engine construction.

    Attributes:
        clarification_enabled: Whether an agent may ask a human a
            clarifying question mid-run.
        scoping_enabled: Whether an agent may ask a human to narrow the
            scope of what it was given.
    """

    clarification_enabled: bool
    scoping_enabled: bool


__all__ = ["EngineBehaviour"]
