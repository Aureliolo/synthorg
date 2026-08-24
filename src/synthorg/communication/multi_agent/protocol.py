"""The contract for invoking an agent inside a multi-party conversation.

``AgentCaller`` is the seam between a conversation and the execution
engine: a conversation decides who speaks and with what prompt, and the
caller turns that into a real LLM dispatch. Keeping it a callable rather
than a service means a conversation never imports the engine layer.
"""

from collections.abc import Awaitable, Callable

from synthorg.communication.multi_agent.models import AgentResponse

AgentCaller = Callable[[str, str, int, str], Awaitable[AgentResponse]]
"""Callback to invoke an agent during a conversation.

Signature: ``(agent_id, prompt, max_tokens, conversation_id) ->
AgentResponse``

``conversation_id`` is threaded through so cost-recording attribution
carries the real conversation identifier per turn instead of a synthetic
placeholder.
"""
