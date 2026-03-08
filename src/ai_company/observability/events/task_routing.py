"""Task routing event constants."""

from typing import Final

TASK_ROUTING_AGENT_SCORED: Final[str] = "task_routing.agent.scored"
TASK_ROUTING_SUBTASK_ROUTED: Final[str] = "task_routing.subtask.routed"
TASK_ROUTING_SUBTASK_UNROUTABLE: Final[str] = "task_routing.subtask.unroutable"
TASK_ROUTING_TOPOLOGY_SELECTED: Final[str] = "task_routing.topology.selected"
TASK_ROUTING_TOPOLOGY_AUTO_RESOLVED: Final[str] = "task_routing.topology.auto_resolved"
TASK_ROUTING_STARTED: Final[str] = "task_routing.started"
TASK_ROUTING_COMPLETE: Final[str] = "task_routing.complete"
TASK_ROUTING_FAILED: Final[str] = "task_routing.failed"
TASK_ROUTING_NO_AGENTS: Final[str] = "task_routing.no_agents"
TASK_ROUTING_SCORER_INVALID_CONFIG: Final[str] = "task_routing.scorer.invalid_config"
