"""Flight recording: pluggable per-turn frame capture for cockpit replay."""

from synthorg.engine.flight_recording.service import (
    FlightRecorderService,
    ReplaySeekView,
)
from synthorg.engine.flight_recording.sink import (
    FlightRecorderSink,
    LiveFlightRecorderSink,
    NoOpFlightRecorderSink,
    PersistenceFlightRecorderSink,
    build_flight_recorder_sink,
    build_frames,
    record_run_frames,
)

__all__ = [
    "FlightRecorderService",
    "FlightRecorderSink",
    "LiveFlightRecorderSink",
    "NoOpFlightRecorderSink",
    "PersistenceFlightRecorderSink",
    "ReplaySeekView",
    "build_flight_recorder_sink",
    "build_frames",
    "record_run_frames",
]
