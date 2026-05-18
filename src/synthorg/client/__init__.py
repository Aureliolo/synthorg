"""Client simulation module for SynthOrg.

Provides the contracts (models, protocols, configs) and concrete
strategy implementations for simulating external clients that
submit requirements and review deliverables.
"""

from synthorg.client.ai_client import AIClient
from synthorg.client.config import (
    ClientPoolConfig,
    ClientSimulationConfig,
    ContinuousModeConfig,
    FeedbackConfig,
    IntakeConfig,
    ReportConfig,
    RequirementGeneratorConfig,
    SimulationRunnerConfig,
)
from synthorg.client.continuous import ContinuousMode
from synthorg.client.factory import (
    UnknownStrategyError,
    build_client_pool_strategy,
    build_entry_point_strategy,
    build_feedback_strategy,
    build_intake_strategy,
    build_report_strategy,
    build_requirement_generator,
)
from synthorg.client.human_client import HumanClient
from synthorg.client.human_queue import (
    HumanInputQueue,
    InMemoryHumanInputQueue,
    PendingRequirement,
    PendingReview,
)
from synthorg.client.hybrid_client import (
    HybridClient,
    HybridRouter,
    default_router,
)
from synthorg.client.models import (
    ClientFeedback,
    ClientProfile,
    ClientRequest,
    GenerationContext,
    PoolConstraints,
    RequestStatus,
    ReviewContext,
    SimulationConfig,
    SimulationMetrics,
    TaskRequirement,
    validate_request_transition,
)
from synthorg.client.protocols import (
    ClientInterface,
    ClientPoolStrategy,
    EntryPointStrategy,
    FeedbackStrategy,
    ReportStrategy,
    RequirementGenerator,
)
from synthorg.client.runner import SimulationRunner

__all__ = [
    "AIClient",
    "ClientFeedback",
    "ClientInterface",
    "ClientPoolConfig",
    "ClientPoolStrategy",
    "ClientProfile",
    "ClientRequest",
    "ClientSimulationConfig",
    "ContinuousMode",
    "ContinuousModeConfig",
    "EntryPointStrategy",
    "FeedbackConfig",
    "FeedbackStrategy",
    "GenerationContext",
    "HumanClient",
    "HumanInputQueue",
    "HybridClient",
    "HybridRouter",
    "InMemoryHumanInputQueue",
    "IntakeConfig",
    "PendingRequirement",
    "PendingReview",
    "PoolConstraints",
    "ReportConfig",
    "ReportStrategy",
    "RequestStatus",
    "RequirementGenerator",
    "RequirementGeneratorConfig",
    "ReviewContext",
    "SimulationConfig",
    "SimulationMetrics",
    "SimulationRunner",
    "SimulationRunnerConfig",
    "TaskRequirement",
    "UnknownStrategyError",
    "build_client_pool_strategy",
    "build_entry_point_strategy",
    "build_feedback_strategy",
    "build_intake_strategy",
    "build_report_strategy",
    "build_requirement_generator",
    "default_router",
    "validate_request_transition",
]
