# module-kind: declarative
"""Where a run's progress, its failures and its live state are published."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from synthorg.budget.coordination_config import ErrorTaxonomyConfig
from synthorg.communication.event_stream.interrupt import InterruptStore
from synthorg.communication.event_stream.stream import EventStreamHub
from synthorg.engine.agent_state_recording import AgentStateRepositoryProvider
from synthorg.engine.classification.protocol import ClassificationSink
from synthorg.engine.middleware.protocol import AgentMiddlewareChain
from synthorg.engine.session import EventReader

if TYPE_CHECKING:
    # A cycle breaker: the flight recorder reaches the red-team report
    # protocol, which reaches the red-team builder, which names
    # ``AgentEngine``, closing the loop this package's import opens.
    from synthorg.engine.flight_recording import FlightRecorderSink


@dataclass(frozen=True, slots=True, kw_only=True)
class EngineObservability:
    """What watches a run from outside it.

    Attributes:
        event_stream_hub: Publishes run frames to a subscribed dashboard,
            or ``None``. Without it a backgrounded run that fails before
            the loop leaves the dashboard hung on "Working".
        event_reader: Reads back a session's events, or ``None``.
        interrupt_store: Where a mid-flight interrupt is delivered, or
            ``None``.
        flight_recorder_sink: Durable per-turn frames, or ``None``.
        agent_state_repository_provider: Reads the LIVE agent-state
            repository. A provider rather than the repository, because a
            run can start before persistence connects and a captured
            ``None`` would leave that agent absent from the live view for
            the life of the process. Carries no default: an engine that
            records no live state is a decision, so
            :func:`no_agent_state` is named rather than assumed.
        classification_sinks: Where failure classifications are written.
            An empty tuple is the declared "nowhere".
        error_taxonomy_config: How a failure is categorised, or ``None``.
        agent_middleware_chain: ``before_agent`` / ``after_agent`` hooks
            at the execution boundary, or ``None``.
    """

    event_stream_hub: EventStreamHub | None
    event_reader: EventReader | None
    interrupt_store: InterruptStore | None
    flight_recorder_sink: FlightRecorderSink | None
    agent_state_repository_provider: AgentStateRepositoryProvider
    classification_sinks: tuple[ClassificationSink, ...]
    error_taxonomy_config: ErrorTaxonomyConfig | None
    agent_middleware_chain: AgentMiddlewareChain | None


__all__ = ["EngineObservability"]
