"""Meeting protocol interface (see Communication design page).

Defines the ``MeetingProtocol`` protocol, the ``ConflictDetector``
protocol, the ``MeetingProtocolFactory`` alias the registry is keyed
on, and the ``AgentCaller`` type alias used to invoke agents during a
meeting without coupling to the engine layer.
"""

from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol, runtime_checkable

from synthorg.communication.meeting.config import MeetingProtocolConfig
from synthorg.communication.meeting.enums import MeetingProtocolType
from synthorg.communication.meeting.models import (
    AgentResponse,
    MeetingAgenda,
    MeetingMinutes,
)
from synthorg.core.task_enums import Priority

AgentCaller = Callable[[str, str, int, str], Awaitable[AgentResponse]]
"""Callback to invoke an agent during a meeting.

Signature: ``(agent_id, prompt, max_tokens, meeting_id) -> AgentResponse``

The orchestrator constructs this from the engine layer, decoupling
protocol implementations from the execution engine.  ``meeting_id``
is threaded through so cost-recording attribution carries the real
meeting identifier per turn instead of a synthetic placeholder.
"""


@runtime_checkable
class RefusingAgentCaller(Protocol):
    """An :data:`AgentCaller` that cannot dispatch, naming what is absent.

    Declared here, beside the alias it narrows, so the orchestrator can ask
    whether its own caller would reach an LLM without importing the module
    that composes real dispatch (which pulls the provider registry and the
    persona renderer into the meeting package's import graph).

    Attributes:
        missing_dependencies: The collaborators absent when it was built.
    """

    missing_dependencies: tuple[str, ...]


TaskCreator = Callable[[str, str | None, Priority], None]
"""Callback to create a task from a meeting action item.

Signature: ``(description, assignee_id, priority: Priority) -> None``

Used by the orchestrator to optionally create tasks from extracted
action items.
"""

ConflictEscalationHook = Callable[[MeetingMinutes], Awaitable[None]]
"""Best-effort post-meeting conflict-resolution hook.

Signature: ``(minutes) -> None`` (awaited)

Invoked by the orchestrator after a completed meeting so a detected
conflict can be fed into the conflict-resolution service. It MUST NOT
raise: the orchestrator awaits it with no surrounding ``try/except``, so
an escaping exception would turn a completed meeting into an unhandled
failure. Implementations own their own error containment.
"""


@runtime_checkable
class ConflictDetector(Protocol):
    """Strategy for detecting conflicts in agent responses.

    Used by ``StructuredPhasesProtocol`` to determine whether a
    discussion round is needed.  The default implementation uses
    keyword matching; alternative implementations might use
    structured JSON output or tool calling for more robust detection.
    """

    def detect(self, response_content: str) -> bool:
        """Determine whether the response indicates conflicts.

        Args:
            response_content: The conflict-check agent response text.

        Returns:
            True if conflicts were detected, False otherwise.
        """
        ...


@runtime_checkable
class MeetingProtocol(Protocol):
    """Strategy interface for meeting protocol implementations.

    Each implementation defines a different structure for how agents
    interact during a meeting (round-robin turns, parallel position
    papers, structured phases with discussion).
    """

    async def run(
        self,
        *,
        meeting_id: str,
        agenda: MeetingAgenda,
        leader_id: str,
        participant_ids: tuple[str, ...],
        agent_caller: AgentCaller,
        token_budget: int,
        lens_assignments: Mapping[str, str] | None = None,
    ) -> MeetingMinutes:
        """Execute the meeting protocol and produce minutes.

        Args:
            meeting_id: Unique identifier for this meeting.
            agenda: The meeting agenda.
            leader_id: ID of the agent leading the meeting.
            participant_ids: IDs of participating agents.
            agent_caller: Callback to invoke agents.
            token_budget: Maximum tokens for the entire meeting.
            lens_assignments: Optional mapping of participant ID to
                strategic lens name.  When provided, protocols inject
                the lens perspective into each participant's prompt.

        Returns:
            Complete meeting minutes.
        """
        ...

    def get_protocol_type(self) -> MeetingProtocolType:
        """Return the protocol type this implementation handles.

        Returns:
            The meeting protocol type enum value.
        """
        ...


MeetingProtocolFactory = Callable[[MeetingProtocolConfig], MeetingProtocol]
"""Builds one protocol instance from one meeting's protocol configuration.

Signature: ``(protocol_config) -> MeetingProtocol``

The registry holds these rather than instances. A shared instance would
carry one configuration for the whole process lifetime, so the sub-config
an operator sets on a meeting type would reach nothing. Each factory
reads only the sub-config matching the protocol it builds, which is the
invariant :class:`MeetingProtocolConfig` documents.
"""
