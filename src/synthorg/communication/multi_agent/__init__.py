"""Primitives for running a multi-party agent conversation.

Three things a conversation needs and none of them is a conversation:
the contract for invoking one agent (:data:`AgentCaller`), what that
agent returns (:class:`AgentResponse`), and the per-round token budget
that bounds the whole exchange (:class:`TokenTracker`).

Kept apart from any one consumer because the contract is the same
whoever is talking: a group chat, a review panel, a debate.
"""

from synthorg.communication.multi_agent.agent_caller import (
    UnknownConversationAgentError,
    build_agent_caller,
)
from synthorg.communication.multi_agent.models import AgentResponse
from synthorg.communication.multi_agent.protocol import AgentCaller
from synthorg.communication.multi_agent.token_tracker import TokenTracker

__all__ = [
    "AgentCaller",
    "AgentResponse",
    "TokenTracker",
    "UnknownConversationAgentError",
    "build_agent_caller",
]
