# module-kind: adapter
"""Concrete planning-tool provider for the owner-run decomposition session.

Fills the :class:`DecompositionToolProvider` seam so the planning session can
ground its plan with live research instead of reasoning purely from priors.
Today it grants the ``web_search`` tool when a provider is configured; the
``owner_id`` / ``project_id`` parameters are the hook for future per-owner
memory recall and per-project brain grants.

The granted ``web_search`` tool is read-only (``EXTERNAL_DATA_REQUEST``), so it
survives the planning session's read-only tool filter; any future write-capable
grant would be dropped there by design.
"""

from synthorg.tools.base import BaseTool
from synthorg.tools.network_validator import NetworkPolicy
from synthorg.tools.web.web_search import WebSearchProvider, WebSearchTool


class PlanningToolProvider:
    """Builds the read-only research tools granted to a planning session.

    Args:
        search_provider: The web-search backend, or ``None`` when web search
            is not configured (the provider then grants no tools).
        network_policy: SSRF policy applied to the granted web_search tool;
            ``None`` uses the tool's conservative default.
    """

    __slots__ = ("_network_policy", "_search_provider")

    def __init__(
        self,
        *,
        search_provider: WebSearchProvider | None,
        network_policy: NetworkPolicy | None = None,
    ) -> None:
        self._search_provider = search_provider
        self._network_policy = network_policy

    def build_tools(
        self,
        *,
        owner_id: str,
        project_id: str | None,
    ) -> tuple[BaseTool, ...]:
        """Return the planning tools granted to *owner_id* for *project_id*.

        Returns:
            The web_search tool when a provider is configured, else an empty
            tuple. ``owner_id`` / ``project_id`` are reserved for future
            per-owner / per-project scoping.
        """
        del owner_id, project_id
        if self._search_provider is None:
            return ()
        return (
            WebSearchTool(
                provider=self._search_provider,
                network_policy=self._network_policy,
            ),
        )
