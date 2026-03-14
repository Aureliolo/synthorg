"""Meeting protocol interface (see Communication design page).

Defines the ``MeetingProtocol`` protocol, the ``ConflictDetector``
protocol, and the ``AgentCaller`` type alias used to invoke agents
during a meeting without coupling to the engine layer.
"""

from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from synthorg.communication.meeting.enums import MeetingProtocolType  # noqa: TC001
from synthorg.communication.meeting.models import (
    AgentResponse,
    MeetingAgenda,
    MeetingMinutes,
)
from synthorg.core.enums import Priority

AgentCaller = Callable[[str, str, int], Awaitable[AgentResponse]]
"""Callback to invoke an agent during a meeting.

Signature: ``(agent_id, prompt, max_tokens) -> AgentResponse``

The orchestrator constructs this from the engine layer, decoupling
protocol implementations from the execution engine.
"""

TaskCreator = Callable[[str, str | None, Priority], None]
"""Callback to create a task from a meeting action item.

Signature: ``(description, assignee_id, priority: Priority) -> None``

Used by the orchestrator to optionally create tasks from extracted
action items.
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

    async def run(  # noqa: PLR0913
        self,
        *,
        meeting_id: str,
        agenda: MeetingAgenda,
        leader_id: str,
        participant_ids: tuple[str, ...],
        agent_caller: AgentCaller,
        token_budget: int,
    ) -> MeetingMinutes:
        """Execute the meeting protocol and produce minutes.

        Args:
            meeting_id: Unique identifier for this meeting.
            agenda: The meeting agenda.
            leader_id: ID of the agent leading the meeting.
            participant_ids: IDs of participating agents.
            agent_caller: Callback to invoke agents.
            token_budget: Maximum tokens for the entire meeting.

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
