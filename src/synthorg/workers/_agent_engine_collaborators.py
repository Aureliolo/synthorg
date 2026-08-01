"""Optional persistence-gated collaborators for the boot ``AgentEngine``.

Both builders here degrade to a safe no-op when persistence is absent so a
persistence-less dev boot (or an empty company) starts cleanly instead of
crashing. They are threaded into ``_construct_agent_engine``.
"""

from collections.abc import Callable

from synthorg.api.state import AppState
from synthorg.docs_engine.state import DocsStateSlice
from synthorg.docs_engine.tool_factory import DocsToolFactory
from synthorg.engine.flight_recording import LiveFlightRecorderSink
from synthorg.engine.intervention import SteeringInbox, build_steering_inbox
from synthorg.engine.state import EngineStateSlice
from synthorg.knowledge.state import KnowledgeStateSlice
from synthorg.knowledge.tool_factory import KnowledgeToolFactory
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrameRepository,
)
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.project_brain.state import ProjectBrainStateSlice
from synthorg.project_brain.tool_factory import ProjectBrainToolFactory
from synthorg.research.state import ResearchStateSlice
from synthorg.research.tool_factory import ResearchToolFactory
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.state import config_resolver_of
from synthorg.tools.structure_map.tool_factory import StructureMapToolFactory

_COCKPIT_NS: str = SettingNamespace.COCKPIT.value
_FR_ENABLED_KEY: str = "flight_recorder_enabled"
_FR_STRATEGY_KEY: str = "flight_recorder_sink_strategy"
_FR_SUMMARY_MAX_CHARS_KEY: str = "flight_recorder_summary_max_chars"


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


def boot_brain_tool_factory_provider(
    app_state: AppState,
) -> Callable[[], ProjectBrainToolFactory | None]:
    """Return a provider reading the live project-brain tool factory.

    The memory-gated project brain wires after the boot ``AgentEngine`` is
    built, so the engine resolves the factory through this provider at
    per-task tool-invoker time rather than capturing a ``None`` at
    construction. The provider returns ``None`` until the brain is wired (or
    forever when it is disabled), in which case no brain tools are added.

    Returns:
        A zero-arg callable returning the current ``ProjectBrainToolFactory``
        from app state, or ``None`` when the brain is not wired.
    """

    def _provider() -> ProjectBrainToolFactory | None:
        return app_state.slice(ProjectBrainStateSlice).tool_factory

    return _provider


def boot_knowledge_tool_factory_provider(
    app_state: AppState,
) -> Callable[[], KnowledgeToolFactory | None]:
    """Return a provider reading the live knowledge tool factory.

    The memory-gated knowledge substrate wires after the boot
    ``AgentEngine`` is built, so the engine resolves the factory through
    this provider at per-task tool-invoker time rather than capturing a
    ``None`` at construction. The provider returns ``None`` until the
    substrate is wired (or forever when it is disabled), in which case no
    knowledge tools are added.

    Returns:
        A zero-arg callable returning the current ``KnowledgeToolFactory``
        from app state, or ``None`` when the substrate is not wired.
    """

    def _provider() -> KnowledgeToolFactory | None:
        return app_state.slice(KnowledgeStateSlice).tool_factory

    return _provider


def boot_docs_tool_factory_provider(
    app_state: AppState,
) -> Callable[[], DocsToolFactory | None]:
    """Return a provider reading the live living-docs tool factory.

    Resolved at per-task tool-invoker time because the docs engine wires
    after the boot ``AgentEngine`` is built.

    Returns:
        A zero-arg callable returning the current ``DocsToolFactory`` from
        app state, or ``None`` when the docs engine is not wired.
    """

    def _provider() -> DocsToolFactory | None:
        return app_state.slice(DocsStateSlice).tool_factory

    return _provider


def boot_research_tool_factory_provider(
    app_state: AppState,
) -> Callable[[], ResearchToolFactory | None]:
    """Return a provider reading the live research tool factory.

    Resolved at per-task tool-invoker time because the research subsystem
    wires after the boot ``AgentEngine`` is built.

    Returns:
        A zero-arg callable returning the current ``ResearchToolFactory``
        from app state, or ``None`` when research is not wired.
    """

    def _provider() -> ResearchToolFactory | None:
        return app_state.slice(ResearchStateSlice).tool_factory

    return _provider


def boot_structure_map_tool_factory_provider(
    app_state: AppState,
) -> Callable[[], StructureMapToolFactory | None]:
    """Return a provider reading the live structure-map tool factory.

    Brownfield intake parks the factory on the engine slice after the
    boot ``AgentEngine`` is built, so the engine resolves it through this
    provider at per-task tool-invoker time.

    Returns:
        A zero-arg callable returning the current
        ``StructureMapToolFactory`` from app state, or ``None`` when no
        codebase has been imported.
    """

    def _provider() -> StructureMapToolFactory | None:
        return app_state.slice(EngineStateSlice).structure_map_tool_factory

    return _provider


def _frame_repository_provider(
    app_state: AppState,
) -> Callable[[], FlightRecorderFrameRepository | None]:
    """Return a provider reading the live frame repository.

    Returns:
        A zero-arg callable returning the current repository, or ``None``
        while persistence is unconnected.
    """

    def _provider() -> FlightRecorderFrameRepository | None:
        backend = app_state.slice(PersistenceStateSlice).backend
        return backend.flight_recorder_frames if backend is not None else None

    return _provider


async def build_boot_flight_recorder_sink(
    app_state: AppState,
) -> LiveFlightRecorderSink:
    """Build the cockpit flight-recorder sink for the boot engine.

    The sink re-picks its delegate per batch from the cockpit settings and
    the live persistence backend, so enabling recording, switching strategy
    or connecting a backend later all take effect on the next run.

    Returns:
        The live flight-recorder sink, seeded with the resolved cockpit
        configuration.
    """
    sink = LiveFlightRecorderSink(_frame_repository_provider(app_state))
    await refresh_flight_recorder_sink(app_state, sink)
    app_state.wire(EngineStateSlice, flight_recorder_sink=sink)
    return sink


async def refresh_flight_recorder_sink(
    app_state: AppState,
    sink: LiveFlightRecorderSink,
) -> None:
    """Resolve the cockpit recorder settings onto *sink*."""
    resolver = config_resolver_of(app_state)
    sink.apply(
        enabled=await resolver.get_bool(_COCKPIT_NS, _FR_ENABLED_KEY),
        strategy=await resolver.get_str(_COCKPIT_NS, _FR_STRATEGY_KEY),
        summary_max_chars=await resolver.get_int(
            _COCKPIT_NS, _FR_SUMMARY_MAX_CHARS_KEY
        ),
    )
