"""Cockpit feature state slice.

Holds the mission-control cockpit service, the flight-recorder query/seek
service, and the steering service. The cockpit and flight-recorder services
wire behind persistence (``_wire_cockpit_services``); the steering service
wires later, after the project brain is up (``_wire_steering_service``), since
it records directives through ``ProjectBrainService``. Controllers and MCP
handlers raise 503 on a ``None`` field.
"""

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice
from synthorg.engine.cockpit import CockpitService
from synthorg.engine.flight_recording import (
    FlightRecorderService,
)
from synthorg.engine.intervention import SteeringNotifier, SteeringService


class CockpitStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the cockpit feature."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    cockpit_service: CockpitService | None = None
    flight_recorder_service: FlightRecorderService | None = None
    steering_service: SteeringService | None = None
    #: Cockpit-channel WS publisher, wired at construction (channels plugin
    #: lives there); consumed by ``_wire_steering_service`` on startup.
    steering_notifier: SteeringNotifier | None = None
