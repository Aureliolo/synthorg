"""Backend-side worker execution service (selected by the runtime builder).

``POST /api/v1/tasks/{task_id}/execute`` delegates to
``WorkerExecutionService.execute_once``. The runtime builder wires
``AgentEngineExecutionService`` when a provider is configured,
``NoProviderExecutionService`` otherwise; ``LifecycleAdvancingExecutionService``
is the lifecycle-only baseline the worker integration tests pin and the
``AppState.worker_execution_service`` lazy fallback.
"""

from synthorg.workers.execution_service._agent_engine import (
    AgentEngineExecutionService,
)
from synthorg.workers.execution_service._lifecycle import (
    LifecycleAdvancingExecutionService,
)
from synthorg.workers.execution_service._no_provider import (
    NoProviderExecutionService,
)
from synthorg.workers.execution_service._protocol import WorkerExecutionService

__all__ = [
    "AgentEngineExecutionService",
    "LifecycleAdvancingExecutionService",
    "NoProviderExecutionService",
    "WorkerExecutionService",
]
