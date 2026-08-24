"""Multi-agent conversation event constants.

Covers the agent-invocation contract and the per-round token budget that
bounds it: the dispatch attempt, its outcome, budget exhaustion, and a
refused invocation.
"""

from typing import Final

MULTI_AGENT_CALLED: Final[str] = "multi_agent.agent.called"
MULTI_AGENT_RESPONDED: Final[str] = "multi_agent.agent.responded"
MULTI_AGENT_CALL_FAILED: Final[str] = "multi_agent.agent.call_failed"
MULTI_AGENT_BUDGET_EXHAUSTED: Final[str] = "multi_agent.budget.exhausted"
MULTI_AGENT_VALIDATION_FAILED: Final[str] = "multi_agent.validation.failed"
