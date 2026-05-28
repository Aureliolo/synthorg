"""Cockpit feature state slice.

Holds the mission-control cockpit service, the flight-recorder query/seek
service, and the steering directive. All three are wired together at boot
(``_wire_cockpit_services``) behind persistence; controllers and MCP
handlers raise 503 on a ``None`` field.
"""

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice
from synthorg.engine.cockpit import CockpitService
from synthorg.engine.flight_recording import (
    FlightRecorderService,
)
from synthorg.engine.intervention import SteeringDirective


class CockpitStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the cockpit feature."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cockpit_service: CockpitService | None = None
    flight_recorder_service: FlightRecorderService | None = None
    steering_directive: SteeringDirective | None = None
