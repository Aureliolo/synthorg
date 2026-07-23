# module-kind: adapter
"""Concrete planning-tool provider for the owner-run decomposition session.

Fills the :class:`DecompositionToolProvider` seam so the planning session can
ground its plan with live research and recall instead of reasoning purely from
priors. It grants:

- ``web_search`` when a web-search provider is configured, and
- a read-only ``search_memory`` tool (fusing the owner's own memory with
  company-wide org knowledge) when a memory backend is wired, so the owner can
  recall past retros and org playbooks the brief already tells it to.

Both granted tools are read-only (``EXTERNAL_DATA_REQUEST`` / ``memory:read``),
so they survive the planning session's read-only tool filter; any future
write-capable grant would be dropped there by design.
"""

from synthorg.core.types import NotBlankStr
from synthorg.memory.org.protocol import OrgMemoryBackend
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.recall_tool import build_memory_recall_tool
from synthorg.tools.base import BaseTool
from synthorg.tools.network_validator import NetworkPolicy
from synthorg.tools.web.web_search import WebSearchProvider, WebSearchTool


class PlanningToolProvider:
    """Builds the read-only research + recall tools granted to a planning session.

    Args:
        search_provider: The web-search backend, or ``None`` when web search
            is not configured (the provider then grants no web tool).
        memory_backend: The owner's agent-memory backend, or ``None`` to grant
            no recall tool.
        org_backend: Org memory fused into recall when present; recall then
            spans the owner's own memory and company-wide knowledge.
        network_policy: SSRF policy applied to the granted web_search tool;
            ``None`` uses the tool's conservative default.
    """

    __slots__ = (
        "_memory_backend",
        "_network_policy",
        "_org_backend",
        "_search_provider",
    )

    def __init__(
        self,
        *,
        search_provider: WebSearchProvider | None,
        memory_backend: MemoryBackend | None = None,
        org_backend: OrgMemoryBackend | None = None,
        network_policy: NetworkPolicy | None = None,
    ) -> None:
        self._search_provider = search_provider
        self._memory_backend = memory_backend
        self._org_backend = org_backend
        self._network_policy = network_policy

    def build_tools(
        self,
        *,
        owner_id: str,
        project_id: str | None,
    ) -> tuple[BaseTool, ...]:
        """Return the planning tools granted to *owner_id* for *project_id*.

        Returns:
            The ``web_search`` tool when a provider is configured and a
            ``search_memory`` tool when a memory backend is wired, in that
            order. ``project_id`` is reserved for future per-project scoping.
        """
        del project_id
        tools: list[BaseTool] = []
        if self._search_provider is not None:
            tools.append(
                WebSearchTool(
                    provider=self._search_provider,
                    network_policy=self._network_policy,
                )
            )
        if self._memory_backend is not None:
            tools.append(
                build_memory_recall_tool(
                    backend=self._memory_backend,
                    agent_id=NotBlankStr(owner_id),
                    org_backend=self._org_backend,
                )
            )
        return tuple(tools)
