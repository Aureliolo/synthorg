"""Meeting protocol subsystem (see Communication design page).

Provides pluggable meeting protocol strategies for structured
multi-agent conversations:

- **Round-Robin**: Sequential turns with full transcript context.
- **Position Papers**: Parallel independent papers, then synthesis.
- **Structured Phases**: Phased agenda with conditional discussion.
"""

from ai_company.communication.meeting.config import (
    MeetingProtocolConfig,
    PositionPapersConfig,
    RoundRobinConfig,
    StructuredPhasesConfig,
)
from ai_company.communication.meeting.enums import (
    MeetingPhase,
    MeetingProtocolType,
    MeetingStatus,
)
from ai_company.communication.meeting.errors import (
    MeetingAgentError,
    MeetingBudgetExhaustedError,
    MeetingError,
    MeetingParticipantError,
    MeetingProtocolNotFoundError,
    MeetingSchedulerError,
    NoParticipantsResolvedError,
    SchedulerAlreadyRunningError,
)
from ai_company.communication.meeting.frequency import MeetingFrequency
from ai_company.communication.meeting.models import (
    ActionItem,
    AgentResponse,
    MeetingAgenda,
    MeetingAgendaItem,
    MeetingContribution,
    MeetingMinutes,
    MeetingRecord,
)
from ai_company.communication.meeting.orchestrator import MeetingOrchestrator
from ai_company.communication.meeting.participant import (
    ParticipantResolver,
    RegistryParticipantResolver,
)
from ai_company.communication.meeting.position_papers import (
    PositionPapersProtocol,
)
from ai_company.communication.meeting.protocol import (
    AgentCaller,
    ConflictDetector,
    MeetingProtocol,
    TaskCreator,
)
from ai_company.communication.meeting.round_robin import RoundRobinProtocol
from ai_company.communication.meeting.scheduler import MeetingScheduler
from ai_company.communication.meeting.structured_phases import (
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
