"""Communication subsystem for the SynthOrg framework."""

import threading
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from synthorg.communication.bus.memory import InMemoryMessageBus
    from synthorg.communication.bus_protocol import MessageBus
    from synthorg.communication.channel import Channel
    from synthorg.communication.config import (
        CircuitBreakerConfig,
        CommunicationConfig,
        HierarchyConfig,
        LoopPreventionConfig,
        MeetingsConfig,
        MessageBusConfig,
        MessageRetentionConfig,
        RateLimitConfig,
    )
    from synthorg.communication.conflict_resolution import (
        Conflict,
        ConflictPosition,
        ConflictResolution,
        ConflictResolutionConfig,
        ConflictResolutionOutcome,
        ConflictResolutionService,
        ConflictResolver,
        DebateConfig,
        DissentRecord,
        HybridConfig,
        JudgeDecision,
        JudgeEvaluator,
    )
    from synthorg.communication.conflict_resolution.authority_strategy import (
        AuthorityResolver,
    )
    from synthorg.communication.conflict_resolution.debate_strategy import (
        DebateResolver,
    )
    from synthorg.communication.conflict_resolution.human_strategy import (
        HumanEscalationResolver,
    )
    from synthorg.communication.conflict_resolution.hybrid_strategy import (
        HybridResolver,
    )
    from synthorg.communication.delegation import (
        AuthorityCheckResult,
        AuthorityValidator,
        DelegationService,
        HierarchyResolver,
    )
    from synthorg.communication.dispatcher import (
        DispatchResult,
        MessageDispatcher,
    )
    from synthorg.communication.enums import (
        ChannelType,
        CommunicationPattern,
        ConflictResolutionStrategy,
        ConflictType,
        MessageBusBackend,
        MessagePriority,
        MessageType,
    )
    from synthorg.communication.errors import (
        ChannelAlreadyExistsError,
        ChannelNotFoundError,
        CommunicationError,
        ConflictHierarchyError,
        ConflictResolutionError,
        ConflictStrategyError,
        DelegationAncestryError,
        DelegationAuthorityError,
        DelegationCircuitOpenError,
        DelegationDepthError,
        DelegationDuplicateError,
        DelegationError,
        DelegationLoopError,
        DelegationRateLimitError,
        HierarchyResolutionError,
        MessageBusAlreadyRunningError,
        MessageBusNotRunningError,
        NotSubscribedError,
    )
    from synthorg.communication.handler import (
        FunctionHandler,
        HandlerRegistration,
        MessageHandler,
        MessageHandlerFunc,
    )
    from synthorg.communication.loop_prevention import (
        CircuitBreakerState,
        DelegationCircuitBreaker,
        DelegationDeduplicator,
        DelegationGuard,
        DelegationRateLimiter,
        GuardCheckOutcome,
        check_ancestry,
        check_delegation_depth,
    )
    from synthorg.communication.meeting import (
        ActionItem,
        AgentCaller,
        AgentResponse,
        ConflictDetector,
        KeywordConflictDetector,
        MeetingAgenda,
        MeetingAgendaItem,
        MeetingAgentError,
        MeetingBudgetExhaustedError,
        MeetingContribution,
        MeetingError,
        MeetingMinutes,
        MeetingOrchestrator,
        MeetingParticipantError,
        MeetingPhase,
        MeetingProtocol,
        MeetingProtocolConfig,
        MeetingProtocolNotFoundError,
        MeetingProtocolType,
        MeetingRecord,
        MeetingStatus,
        PositionPapersConfig,
        PositionPapersProtocol,
        RoundRobinConfig,
        RoundRobinProtocol,
        StructuredPhasesConfig,
        StructuredPhasesProtocol,
        TaskCreator,
    )
    from synthorg.communication.meeting.config import MeetingTypeConfig
    from synthorg.communication.message import (
        DataPart,
        FilePart,
        Message,
        MessageMetadata,
        Part,
        TextPart,
        UriPart,
    )
    from synthorg.communication.messenger import AgentMessenger
    from synthorg.communication.subscription import (
        DeliveryEnvelope,
        Subscription,
    )

# name -> (module path, attribute) for PEP 562 lazy resolution. The eager
# re-exports pulled the heavy engine / communication graph in at package
# import, so importing a light leaf under this package dragged the whole
# subgraph and could close a cross-package import cycle (ADR-0012). Resolving
# on first access keeps ``from synthorg.communication import Name`` working
# unchanged.
_LAZY_EXPORTS: Final[dict[str, tuple[str, str]]] = {
    "InMemoryMessageBus": (
        "synthorg.communication.bus.memory",
        "InMemoryMessageBus",
    ),
    "MessageBus": ("synthorg.communication.bus_protocol", "MessageBus"),
    "Channel": ("synthorg.communication.channel", "Channel"),
    "CircuitBreakerConfig": (
        "synthorg.communication.config",
        "CircuitBreakerConfig",
    ),
    "CommunicationConfig": (
        "synthorg.communication.config",
        "CommunicationConfig",
    ),
    "HierarchyConfig": ("synthorg.communication.config", "HierarchyConfig"),
    "LoopPreventionConfig": (
        "synthorg.communication.config",
        "LoopPreventionConfig",
    ),
    "MeetingsConfig": ("synthorg.communication.config", "MeetingsConfig"),
    "MessageBusConfig": ("synthorg.communication.config", "MessageBusConfig"),
    "MessageRetentionConfig": (
        "synthorg.communication.config",
        "MessageRetentionConfig",
    ),
    "RateLimitConfig": ("synthorg.communication.config", "RateLimitConfig"),
    "Conflict": ("synthorg.communication.conflict_resolution", "Conflict"),
    "ConflictPosition": (
        "synthorg.communication.conflict_resolution",
        "ConflictPosition",
    ),
    "ConflictResolution": (
        "synthorg.communication.conflict_resolution",
        "ConflictResolution",
    ),
    "ConflictResolutionConfig": (
        "synthorg.communication.conflict_resolution",
        "ConflictResolutionConfig",
    ),
    "ConflictResolutionOutcome": (
        "synthorg.communication.conflict_resolution",
        "ConflictResolutionOutcome",
    ),
    "ConflictResolutionService": (
        "synthorg.communication.conflict_resolution",
        "ConflictResolutionService",
    ),
    "ConflictResolver": (
        "synthorg.communication.conflict_resolution",
        "ConflictResolver",
    ),
    "DebateConfig": (
        "synthorg.communication.conflict_resolution",
        "DebateConfig",
    ),
    "DissentRecord": (
        "synthorg.communication.conflict_resolution",
        "DissentRecord",
    ),
    "HybridConfig": (
        "synthorg.communication.conflict_resolution",
        "HybridConfig",
    ),
    "JudgeDecision": (
        "synthorg.communication.conflict_resolution",
        "JudgeDecision",
    ),
    "JudgeEvaluator": (
        "synthorg.communication.conflict_resolution",
        "JudgeEvaluator",
    ),
    "AuthorityResolver": (
        "synthorg.communication.conflict_resolution.authority_strategy",
        "AuthorityResolver",
    ),
    "DebateResolver": (
        "synthorg.communication.conflict_resolution.debate_strategy",
        "DebateResolver",
    ),
    "HumanEscalationResolver": (
        "synthorg.communication.conflict_resolution.human_strategy",
        "HumanEscalationResolver",
    ),
    "HybridResolver": (
        "synthorg.communication.conflict_resolution.hybrid_strategy",
        "HybridResolver",
    ),
    "AuthorityCheckResult": (
        "synthorg.communication.delegation",
        "AuthorityCheckResult",
    ),
    "AuthorityValidator": (
        "synthorg.communication.delegation",
        "AuthorityValidator",
    ),
    "DelegationService": (
        "synthorg.communication.delegation",
        "DelegationService",
    ),
    "HierarchyResolver": (
        "synthorg.communication.delegation",
        "HierarchyResolver",
    ),
    "DispatchResult": ("synthorg.communication.dispatcher", "DispatchResult"),
    "MessageDispatcher": (
        "synthorg.communication.dispatcher",
        "MessageDispatcher",
    ),
    "ChannelType": ("synthorg.communication.enums", "ChannelType"),
    "CommunicationPattern": (
        "synthorg.communication.enums",
        "CommunicationPattern",
    ),
    "ConflictResolutionStrategy": (
        "synthorg.communication.enums",
        "ConflictResolutionStrategy",
    ),
    "ConflictType": ("synthorg.communication.enums", "ConflictType"),
    "MessageBusBackend": (
        "synthorg.communication.enums",
        "MessageBusBackend",
    ),
    "MessagePriority": ("synthorg.communication.enums", "MessagePriority"),
    "MessageType": ("synthorg.communication.enums", "MessageType"),
    "ChannelAlreadyExistsError": (
        "synthorg.communication.errors",
        "ChannelAlreadyExistsError",
    ),
    "ChannelNotFoundError": (
        "synthorg.communication.errors",
        "ChannelNotFoundError",
    ),
    "CommunicationError": (
        "synthorg.communication.errors",
        "CommunicationError",
    ),
    "ConflictHierarchyError": (
        "synthorg.communication.errors",
        "ConflictHierarchyError",
    ),
    "ConflictResolutionError": (
        "synthorg.communication.errors",
        "ConflictResolutionError",
    ),
    "ConflictStrategyError": (
        "synthorg.communication.errors",
        "ConflictStrategyError",
    ),
    "DelegationAncestryError": (
        "synthorg.communication.errors",
        "DelegationAncestryError",
    ),
    "DelegationAuthorityError": (
        "synthorg.communication.errors",
        "DelegationAuthorityError",
    ),
    "DelegationCircuitOpenError": (
        "synthorg.communication.errors",
        "DelegationCircuitOpenError",
    ),
    "DelegationDepthError": (
        "synthorg.communication.errors",
        "DelegationDepthError",
    ),
    "DelegationDuplicateError": (
        "synthorg.communication.errors",
        "DelegationDuplicateError",
    ),
    "DelegationError": ("synthorg.communication.errors", "DelegationError"),
    "DelegationLoopError": (
        "synthorg.communication.errors",
        "DelegationLoopError",
    ),
    "DelegationRateLimitError": (
        "synthorg.communication.errors",
        "DelegationRateLimitError",
    ),
    "HierarchyResolutionError": (
        "synthorg.communication.errors",
        "HierarchyResolutionError",
    ),
    "MessageBusAlreadyRunningError": (
        "synthorg.communication.errors",
        "MessageBusAlreadyRunningError",
    ),
    "MessageBusNotRunningError": (
        "synthorg.communication.errors",
        "MessageBusNotRunningError",
    ),
    "NotSubscribedError": (
        "synthorg.communication.errors",
        "NotSubscribedError",
    ),
    "FunctionHandler": ("synthorg.communication.handler", "FunctionHandler"),
    "HandlerRegistration": (
        "synthorg.communication.handler",
        "HandlerRegistration",
    ),
    "MessageHandler": ("synthorg.communication.handler", "MessageHandler"),
    "MessageHandlerFunc": (
        "synthorg.communication.handler",
        "MessageHandlerFunc",
    ),
    "CircuitBreakerState": (
        "synthorg.communication.loop_prevention",
        "CircuitBreakerState",
    ),
    "DelegationCircuitBreaker": (
        "synthorg.communication.loop_prevention",
        "DelegationCircuitBreaker",
    ),
    "DelegationDeduplicator": (
        "synthorg.communication.loop_prevention",
        "DelegationDeduplicator",
    ),
    "DelegationGuard": (
        "synthorg.communication.loop_prevention",
        "DelegationGuard",
    ),
    "DelegationRateLimiter": (
        "synthorg.communication.loop_prevention",
        "DelegationRateLimiter",
    ),
    "GuardCheckOutcome": (
        "synthorg.communication.loop_prevention",
        "GuardCheckOutcome",
    ),
    "check_ancestry": (
        "synthorg.communication.loop_prevention",
        "check_ancestry",
    ),
    "check_delegation_depth": (
        "synthorg.communication.loop_prevention",
        "check_delegation_depth",
    ),
    "ActionItem": ("synthorg.communication.meeting", "ActionItem"),
    "AgentCaller": ("synthorg.communication.meeting", "AgentCaller"),
    "AgentResponse": ("synthorg.communication.meeting", "AgentResponse"),
    "ConflictDetector": (
        "synthorg.communication.meeting",
        "ConflictDetector",
    ),
    "KeywordConflictDetector": (
        "synthorg.communication.meeting",
        "KeywordConflictDetector",
    ),
    "MeetingAgenda": ("synthorg.communication.meeting", "MeetingAgenda"),
    "MeetingAgendaItem": (
        "synthorg.communication.meeting",
        "MeetingAgendaItem",
    ),
    "MeetingAgentError": (
        "synthorg.communication.meeting",
        "MeetingAgentError",
    ),
    "MeetingBudgetExhaustedError": (
        "synthorg.communication.meeting",
        "MeetingBudgetExhaustedError",
    ),
    "MeetingContribution": (
        "synthorg.communication.meeting",
        "MeetingContribution",
    ),
    "MeetingError": ("synthorg.communication.meeting", "MeetingError"),
    "MeetingMinutes": ("synthorg.communication.meeting", "MeetingMinutes"),
    "MeetingOrchestrator": (
        "synthorg.communication.meeting",
        "MeetingOrchestrator",
    ),
    "MeetingParticipantError": (
        "synthorg.communication.meeting",
        "MeetingParticipantError",
    ),
    "MeetingPhase": ("synthorg.communication.meeting", "MeetingPhase"),
    "MeetingProtocol": ("synthorg.communication.meeting", "MeetingProtocol"),
    "MeetingProtocolConfig": (
        "synthorg.communication.meeting",
        "MeetingProtocolConfig",
    ),
    "MeetingProtocolNotFoundError": (
        "synthorg.communication.meeting",
        "MeetingProtocolNotFoundError",
    ),
    "MeetingProtocolType": (
        "synthorg.communication.meeting",
        "MeetingProtocolType",
    ),
    "MeetingRecord": ("synthorg.communication.meeting", "MeetingRecord"),
    "MeetingStatus": ("synthorg.communication.meeting", "MeetingStatus"),
    "PositionPapersConfig": (
        "synthorg.communication.meeting",
        "PositionPapersConfig",
    ),
    "PositionPapersProtocol": (
        "synthorg.communication.meeting",
        "PositionPapersProtocol",
    ),
    "RoundRobinConfig": (
        "synthorg.communication.meeting",
        "RoundRobinConfig",
    ),
    "RoundRobinProtocol": (
        "synthorg.communication.meeting",
        "RoundRobinProtocol",
    ),
    "StructuredPhasesConfig": (
        "synthorg.communication.meeting",
        "StructuredPhasesConfig",
    ),
    "StructuredPhasesProtocol": (
        "synthorg.communication.meeting",
        "StructuredPhasesProtocol",
    ),
    "TaskCreator": ("synthorg.communication.meeting", "TaskCreator"),
    "MeetingTypeConfig": (
        "synthorg.communication.meeting.config",
        "MeetingTypeConfig",
    ),
    "DataPart": ("synthorg.communication.message", "DataPart"),
    "FilePart": ("synthorg.communication.message", "FilePart"),
    "Message": ("synthorg.communication.message", "Message"),
    "MessageMetadata": (
        "synthorg.communication.message",
        "MessageMetadata",
    ),
    "Part": ("synthorg.communication.message", "Part"),
    "TextPart": ("synthorg.communication.message", "TextPart"),
    "UriPart": ("synthorg.communication.message", "UriPart"),
    "AgentMessenger": ("synthorg.communication.messenger", "AgentMessenger"),
    "DeliveryEnvelope": (
        "synthorg.communication.subscription",
        "DeliveryEnvelope",
    ),
    "Subscription": ("synthorg.communication.subscription", "Subscription"),
}

_LAZY_EXPORT_LOCK: Final[threading.Lock] = threading.Lock()


def __getattr__(name: str) -> object:
    """Resolve and cache a lazily-exported symbol on first access (PEP 562).

    Returns:
        The resolved (and now cached) export object for ``name``.

    Raises:
        AttributeError: When ``name`` is not a known lazy export.
    """
    if name not in _LAZY_EXPORTS:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    import importlib  # noqa: PLC0415

    with _LAZY_EXPORT_LOCK:
        if name in globals():
            return globals()[name]
        module_path, attr = _LAZY_EXPORTS[name]
        value = getattr(importlib.import_module(module_path), attr)
        globals()[name] = value
        return value


def __dir__() -> list[str]:
    """Include the lazily-exported names in ``dir()`` / autocomplete.

    Returns:
        The sorted list of public export names.
    """
    return sorted(__all__)


__all__ = [
    "ActionItem",
    "AgentCaller",
    "AgentMessenger",
    "AgentResponse",
    "AuthorityCheckResult",
    "AuthorityResolver",
    "AuthorityValidator",
    "Channel",
    "ChannelAlreadyExistsError",
    "ChannelNotFoundError",
    "ChannelType",
    "CircuitBreakerConfig",
    "CircuitBreakerState",
    "CommunicationConfig",
    "CommunicationError",
    "CommunicationPattern",
    "Conflict",
    "ConflictDetector",
    "ConflictHierarchyError",
    "ConflictPosition",
    "ConflictResolution",
    "ConflictResolutionConfig",
    "ConflictResolutionError",
    "ConflictResolutionOutcome",
    "ConflictResolutionService",
    "ConflictResolutionStrategy",
    "ConflictResolver",
    "ConflictStrategyError",
    "ConflictType",
    "DataPart",
    "DebateConfig",
    "DebateResolver",
    "DelegationAncestryError",
    "DelegationAuthorityError",
    "DelegationCircuitBreaker",
    "DelegationCircuitOpenError",
    "DelegationDeduplicator",
    "DelegationDepthError",
    "DelegationDuplicateError",
    "DelegationError",
    "DelegationGuard",
    "DelegationLoopError",
    "DelegationRateLimiter",
    "DelegationService",
    "DeliveryEnvelope",
    "DispatchResult",
    "DissentRecord",
    "FilePart",
    "FunctionHandler",
    "GuardCheckOutcome",
    "HandlerRegistration",
    "HierarchyConfig",
    "HierarchyResolutionError",
    "HierarchyResolver",
    "HumanEscalationResolver",
    "HybridConfig",
    "HybridResolver",
    "InMemoryMessageBus",
    "JudgeDecision",
    "JudgeEvaluator",
    "KeywordConflictDetector",
    "LoopPreventionConfig",
    "MeetingAgenda",
    "MeetingAgendaItem",
    "MeetingAgentError",
    "MeetingBudgetExhaustedError",
    "MeetingContribution",
    "MeetingError",
    "MeetingMinutes",
    "MeetingOrchestrator",
    "MeetingParticipantError",
    "MeetingPhase",
    "MeetingProtocol",
    "MeetingProtocolConfig",
    "MeetingProtocolNotFoundError",
    "MeetingProtocolType",
    "MeetingRecord",
    "MeetingStatus",
    "MeetingTypeConfig",
    "MeetingsConfig",
    "Message",
    "MessageBus",
    "MessageBusAlreadyRunningError",
    "MessageBusBackend",
    "MessageBusConfig",
    "MessageBusNotRunningError",
    "MessageDispatcher",
    "MessageHandler",
    "MessageHandlerFunc",
    "MessageMetadata",
    "MessagePriority",
    "MessageRetentionConfig",
    "MessageType",
    "NotSubscribedError",
    "Part",
    "PositionPapersConfig",
    "PositionPapersProtocol",
    "RateLimitConfig",
    "RoundRobinConfig",
    "RoundRobinProtocol",
    "StructuredPhasesConfig",
    "StructuredPhasesProtocol",
    "Subscription",
    "TaskCreator",
    "TextPart",
    "UriPart",
    "check_ancestry",
    "check_delegation_depth",
]
