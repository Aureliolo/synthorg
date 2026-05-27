"""Telemetry feature state slice.

Holds the opt-in telemetry collector. ``None`` until built at boot (and
only functional when telemetry is enabled); the health and capabilities
controllers read it defensively.
"""

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice
from synthorg.telemetry.collector import TelemetryCollector  # noqa: TC001


class TelemetryStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the telemetry feature."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    collector: TelemetryCollector | None = None
