"""Optional persistence-gated collaborators for the boot ``AgentEngine``.

Both builders here degrade to a safe no-op when persistence is absent so a
persistence-less dev boot (or an empty company) starts cleanly instead of
crashing. They are threaded into ``_construct_agent_engine``.
"""

from collections.abc import Callable

from synthorg.api.state import AppState
from synthorg.docs_engine.state import DocsStateSlice
from synthorg.docs_engine.tool_factory import DocsToolFactory
from synthorg.engine.flight_recording import (
    FlightRecorderSink,
    build_flight_recorder_sink,
)
from synthorg.engine.intervention import SteeringInbox, build_steering_inbox
from synthorg.knowledge.state import KnowledgeStateSlice
from synthorg.knowledge.tool_factory import KnowledgeToolFactory
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.project_brain.state import ProjectBrainStateSlice
from synthorg.project_brain.tool_factory import ProjectBrainToolFactory
from synthorg.research.state import ResearchStateSlice
from synthorg.research.tool_factory import ResearchToolFactory
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.state import config_resolver_of

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
