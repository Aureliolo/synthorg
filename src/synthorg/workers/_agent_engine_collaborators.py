"""Optional persistence-gated collaborators for the boot ``AgentEngine``.

Both builders here degrade to a safe no-op when persistence is absent so a
persistence-less dev boot (or an empty company) starts cleanly instead of
crashing. They are threaded into ``_construct_agent_engine``.
"""

from typing import TYPE_CHECKING

from synthorg.engine.flight_recording import (
    FlightRecorderSink,
    build_flight_recorder_sink,
)
from synthorg.engine.intervention import SteeringInbox, build_steering_inbox
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.state import config_resolver_of

if TYPE_CHECKING:
    from synthorg.api.state import AppState

_COCKPIT_NS: str = SettingNamespace.COCKPIT.value
_FR_ENABLED_KEY: str = "flight_recorder_enabled"
_FR_STRATEGY_KEY: str = "flight_recorder_sink_strategy"


def boot_steering_inbox(app_state: AppState) -> SteeringInbox | None:
    """Build the steering inbox from the connected persistence backend.

    The inbox reads active project-brain steering directives at safe
    boundaries. It needs only persistence (not the memory-backend-gated
    brain service), so it is available whenever a backend is connected.

    Returns:
        A brain-backed steering inbox, or ``None`` when persistence is
        absent (an empty-company or persistence-less dev boot).
    """
    backend = app_state.slice(PersistenceStateSlice).backend
    if backend is None:
        return None
    return build_steering_inbox(backend.project_brain)


async def build_boot_flight_recorder_sink(app_state: AppState) -> FlightRecorderSink:
    """Resolve the cockpit flight-recorder sink for the boot engine.

    Reads the cockpit ``flight_recorder_enabled`` flag and the
    ``flight_recorder_sink_strategy`` discriminator via the async
    resolver (DB > env > default), and supplies the persistence-backed
    frame repository only when persistence is connected. Without
    persistence the factory degrades to the no-op sink, so a
    persistence-less dev boot records nothing instead of crashing.

    Returns:
        The configured flight-recorder sink (a no-op sink when disabled
        or persistence is absent).
    """
    backend = app_state.slice(PersistenceStateSlice).backend
    repository = backend.flight_recorder_frames if backend is not None else None
    enabled = await config_resolver_of(app_state).get_bool(_COCKPIT_NS, _FR_ENABLED_KEY)
    strategy = await config_resolver_of(app_state).get_str(
        _COCKPIT_NS, _FR_STRATEGY_KEY
    )
    return build_flight_recorder_sink(
        repository,
        enabled=enabled,
        strategy=strategy,
    )
