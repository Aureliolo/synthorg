# module-kind: declarative
"""The organisation the engine runs inside: who exists, and what they may do."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from synthorg.engine.mcp_self_consumer import MCPSelfConsumerProvider
from synthorg.engine.routing_policy.capability_policy import CapabilityPolicy
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.registry_protocol import AgentRegistryProtocol
from synthorg.persistence.project_protocol import ProjectRepository

if TYPE_CHECKING:
    # Cycle breakers: the coordinator runs sub-agents ON an ``AgentEngine``
    # and the evolution service is reached from its post-execution hook, so
    # both name it again on their own import paths.
    from synthorg.engine.coordination.service import MultiAgentCoordinator
    from synthorg.engine.evolution.service import EvolutionService


@dataclass(frozen=True, slots=True, kw_only=True)
class EngineOrg:
    """The roster, the board, and what an agent may reach beyond its tools.

    Attributes:
        agent_registry: The live roster. With a task engine it also arms
            blocking sub-agent delegation; without either, delegation is
            off.
        capability: The SAME policy selection judged against, so a task
            the ladder assigned always clears here. ``None`` leaves a
            hand-assigned pair unjudged.
        task_engine: The central engine task transitions sync to, or
            ``None`` to keep every transition local.
        project_repo: Validates a work task's project before it runs. A
            work task refuses to run without it.
        coordinator: The multi-agent coordinator ``coordinate()``
            delegates to, or ``None``.
        evolution_service: The post-execution identity-evolution trigger,
            or ``None`` when ``evolution.enabled`` is off.
        mcp_self_consumer: Adds trust-scoped SynthOrg MCP tools to an
            agent's registry, or ``None`` (mode DISABLED), which is a
            no-op.
    """

    agent_registry: AgentRegistryProtocol | None
    capability: CapabilityPolicy | None
    task_engine: TaskEngine | None
    project_repo: ProjectRepository | None
    coordinator: MultiAgentCoordinator | None
    evolution_service: EvolutionService | None
    mcp_self_consumer: MCPSelfConsumerProvider | None


__all__ = ["EngineOrg"]
