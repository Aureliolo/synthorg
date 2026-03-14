"""Meeting protocol subsystem (see Communication design page).

Provides pluggable meeting protocol strategies for structured
multi-agent conversations:

- **Round-Robin**: Sequential turns with full transcript context.
- **Position Papers**: Parallel independent papers, then synthesis.
- **Structured Phases**: Phased agenda with conditional discussion.
"""

from synthorg.communication.meeting.config import (
    MeetingProtocolConfig,
    PositionPapersConfig,
    RoundRobinConfig,
    StructuredPhasesConfig,
)
from synthorg.communication.meeting.enums import (
    MeetingPhase,
    MeetingProtocolType,
    MeetingStatus,
)
from synthorg.communication.meeting.errors import (
    MeetingAgentError,
    MeetingBudgetExhaustedError,
    MeetingError,
    MeetingParticipantError,
    MeetingProtocolNotFoundError,
    MeetingSchedulerError,
    NoParticipantsResolvedError,
    SchedulerAlreadyRunningError,
)
from synthorg.communication.meeting.frequency import MeetingFrequency
from synthorg.communication.meeting.models import (
    ActionItem,
    AgentResponse,
    MeetingAgenda,
    MeetingAgendaItem,
    MeetingContribution,
    MeetingMinutes,
    MeetingRecord,
)
from synthorg.communication.meeting.orchestrator import MeetingOrchestrator
from synthorg.communication.meeting.participant import (
    ParticipantResolver,
    RegistryParticipantResolver,
)
from synthorg.communication.meeting.position_papers import (
    PositionPapersProtocol,
)
from synthorg.communication.meeting.protocol import (
    AgentCaller,
    ConflictDetector,
    MeetingProtocol,
    TaskCreator,
)
from synthorg.communication.meeting.round_robin import RoundRobinProtocol
from synthorg.communication.meeting.scheduler import MeetingScheduler
from synthorg.communication.meeting.structured_phases import (
    KeywordConflictDetector,
    StructuredPhasesProtocol,
)

__all__ = [
    "ActionItem",
    "AgentCaller",
    "AgentResponse",
    "ConflictDetector",
    "KeywordConflictDetector",
    "MeetingAgenda",
    "MeetingAgendaItem",
    "MeetingAgentError",
    "MeetingBudgetExhaustedError",
    "MeetingContribution",
    "MeetingError",
    "MeetingFrequency",
    "MeetingMinutes",
    "MeetingOrchestrator",
    "MeetingParticipantError",
    "MeetingPhase",
    "MeetingProtocol",
    "MeetingProtocolConfig",
    "MeetingProtocolNotFoundError",
    "MeetingProtocolType",
    "MeetingRecord",
    "MeetingScheduler",
    "MeetingSchedulerError",
    "MeetingStatus",
    "NoParticipantsResolvedError",
    "ParticipantResolver",
    "PositionPapersConfig",
    "PositionPapersProtocol",
    "RegistryParticipantResolver",
    "RoundRobinConfig",
    "RoundRobinProtocol",
    "SchedulerAlreadyRunningError",
    "StructuredPhasesConfig",
    "StructuredPhasesProtocol",
    "TaskCreator",
]
