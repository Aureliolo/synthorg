"""Blocking sub-agent delegation.

A supervising agent's ``delegate_and_await`` tool offloads a focused
sub-task to a child agent, runs it to completion inline via the shared
:class:`~synthorg.engine.agent_engine.AgentEngine`, and folds the child's
answer plus a bounded transcript back into the supervisor's turn.
"""

from synthorg.engine.delegation.errors import (
    DelegationError,
    DelegationTargetNotFoundError,
)
from synthorg.engine.delegation.models import DelegationResult, DelegationSpec
from synthorg.engine.delegation.protocol import SubAgentRunner

# NOTE: ``InProcessSubAgentRunner`` is deliberately NOT imported here.
# It depends on the concrete ``AgentEngine`` at module level, and this
# package's ``protocol`` is imported (via ``_agent_tool_registry``) while
# ``agent_engine`` is still loading; eagerly pulling the runner in would
# close an ``agent_engine`` import cycle. Import it from
# ``synthorg.engine.delegation.runner`` directly (the engine does so lazily).

__all__ = [
    "DelegationError",
    "DelegationResult",
    "DelegationSpec",
    "DelegationTargetNotFoundError",
    "SubAgentRunner",
]
