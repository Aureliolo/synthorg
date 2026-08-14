# module-kind: code
"""The recording manifest: which loops, on which models, how many times.

Mirrors :mod:`evals.benchmark_scoring`'s manifest pattern, so the A/B is driven
by a committed declarative file rather than by flags a maintainer has to
remember. Changing the matrix is a reviewable diff, and the manifest's digest is
stamped into the scoreboard so a ranking can always be traced to the matrix that
produced it.

The loop list is validated against the live registry rather than a hardcoded
set: the acceptance criterion is that the A/B covers *every* registered loop, so
a manifest that silently omits one is refused.
"""

from pathlib import Path
from typing import Final, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.engine.loop_selector import registered_loop_types

#: Repetitions per (loop, capability, brief) cell when the manifest does not say.
#: Three is the smallest count that yields a median resistant to one outlier.
DEFAULT_REPETITIONS: Final[int] = 3


class CapabilityEntry(BaseModel):
    """One model capability every loop is measured on.

    A capability binds an explicit ``(provider, model_id)`` pair. The pair is never
    inferred or auto-picked, matching the Explicit Provider Binding rule the
    gateway enforces at token-mint time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    capability: NotBlankStr
    provider: NotBlankStr
    model_id: NotBlankStr


class LoopAbManifest(BaseModel):
    """The committed A/B recording matrix."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    brief_suite: NotBlankStr
    loops: tuple[NotBlankStr, ...] = Field(min_length=1)
    capabilities: tuple[CapabilityEntry, ...] = Field(min_length=1)
    repetitions: int = Field(default=DEFAULT_REPETITIONS, gt=0)

    @model_validator(mode="after")
    def _loops_cover_the_registry(self) -> Self:
        """Refuse a manifest naming an unknown loop or omitting a registered one.

        Both directions matter. An unknown name is a typo that would silently
        shrink the comparison; a missing name would publish a scoreboard that
        looks complete while leaving a shipped loop unmeasured.
        """
        registered = set(registered_loop_types())
        declared = set(self.loops)
        if len(declared) != len(self.loops):
            # A duplicated loop passes the set-based registry checks below but
            # multiplies planned_runs by len(self.loops), silently doubling that
            # loop's real-spend runs.
            msg = f"manifest declares duplicate loop names: {sorted(self.loops)}"
            raise ValueError(msg)
        if unknown := declared - registered:
            msg = (
                f"manifest names unregistered loop(s) {sorted(unknown)}; "
                f"registered: {sorted(registered)}"
            )
            raise ValueError(msg)
        if missing := registered - declared:
            msg = (
                f"manifest omits registered loop(s) {sorted(missing)}; the A/B "
                "must compare every loop that ships, or the scoreboard "
                "understates the field"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _capabilities_are_distinct(self) -> Self:
        """Reject a duplicated label, which would collide in the scoreboard."""
        labels = [capability.capability for capability in self.capabilities]
        if len(set(labels)) != len(labels):
            msg = f"manifest declares duplicate capability labels: {sorted(labels)}"
            raise ValueError(msg)
        return self

    @property
    def planned_runs(self) -> int:
        """Total runs this manifest schedules, before briefs are counted in."""
        return len(self.loops) * len(self.capabilities) * self.repetitions


def load_manifest(path: Path) -> LoopAbManifest:
    """Parse and validate the recording manifest YAML.

    Returns:
        The validated :class:`LoopAbManifest`.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return LoopAbManifest.model_validate(raw)


__all__ = [
    "DEFAULT_REPETITIONS",
    "CapabilityEntry",
    "LoopAbManifest",
    "load_manifest",
]
