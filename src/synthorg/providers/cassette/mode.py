"""Cassette mode + configuration.

Resolved once at the boot site (env > code default) and passed into
:meth:`ProviderRegistry.from_config`. Compose-set on purpose: switching
mode mid-process would leave a half-recorded / half-replayed run, which
is worse than recreating the container against the mode you want.
"""

from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CassetteMode(StrEnum):
    """Whether the provider seam records, replays, or is inert."""

    OFF = "off"
    RECORD = "record"
    REPLAY = "replay"


class CassetteConfig(BaseModel):
    """Boot-time cassette configuration.

    ``path`` is mandatory whenever the seam is active: a default shared
    path would let two runs silently clobber each other's cassette
    (and would collide under xdist), so it must be chosen explicitly.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    mode: CassetteMode = Field(
        default=CassetteMode.OFF,
        description="Record, replay, or inert",
    )
    path: Path | None = Field(
        default=None,
        description="Cassette file path; required when mode != off",
    )

    @model_validator(mode="after")
    def _require_path_when_active(self) -> Self:
        """Reject an active mode without a cassette path.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If the mode is ``RECORD`` or ``REPLAY`` but
                ``path`` is ``None``.
        """
        if self.mode is not CassetteMode.OFF and self.path is None:
            msg = f"cassette path is required when mode is {self.mode.value!r}"
            raise ValueError(msg)
        return self

    @property
    def is_active(self) -> bool:
        """True when the seam should wrap providers."""
        return self.mode is not CassetteMode.OFF


__all__ = [
    "CassetteConfig",
    "CassetteMode",
]
