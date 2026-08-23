"""The contract for invoking an agent inside a multi-party conversation.

``AgentCaller`` is the seam between a conversation and the execution
engine: a conversation decides who speaks and with what prompt, and the
caller turns that into a real LLM dispatch. Keeping it a callable rather
than a service means a conversation never imports the engine layer.

``RefusingAgentCaller`` narrows the alias to the caller a composition
root builds when a collaborator it needed was absent. It names what was
missing, so a conversation can report why it cannot reach a model rather
than discovering it one dispatch at a time.
"""

from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from synthorg.communication.multi_agent.models import AgentResponse

AgentCaller = Callable[[str, str, int, str], Awaitable[AgentResponse]]
"""Callback to invoke an agent during a conversation.

Signature: ``(agent_id, prompt, max_tokens, conversation_id) ->
AgentResponse``

``conversation_id`` is threaded through so cost-recording attribution
carries the real conversation identifier per turn instead of a synthetic
placeholder.
"""


@runtime_checkable
class RefusingAgentCaller(Protocol):
    """An :data:`AgentCaller` that cannot dispatch, naming what is absent.

    Declared beside the alias it narrows so a caller's holder can ask
    whether it would reach an LLM without importing the module that
    composes real dispatch.

    Attributes:
        missing_dependencies: The collaborators absent when it was built.
    """

    missing_dependencies: tuple[str, ...]
