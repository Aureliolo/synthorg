"""Planning-tool provider protocol for the agent-session decomposer.

A light leaf so both the strategy and the coordinator factory can name the
provider without importing the heavy agent-session module (which pulls the
ReAct loop, tool invoker, and provider stack).
"""

from typing import Protocol, runtime_checkable

from synthorg.tools.base import BaseTool


@runtime_checkable
class DecompositionToolProvider(Protocol):
    """Builds the read/research tools granted to a planning session.

    Implementations return the owner's grantable planning tools (memory
    recall, project-brain search, web search when configured, ...). The
    provider is optional: when absent the planning session runs with only the
    terminal submit tool, still reasoning across turns before it plans.
    """

    def build_tools(
        self,
        *,
        owner_id: str,
        project_id: str | None,
    ) -> tuple[BaseTool, ...]:
        """Return the planning tools granted to *owner_id* for *project_id*."""
        ...
