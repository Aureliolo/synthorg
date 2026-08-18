# module-kind: adapter
"""Concrete planning-tool provider for the owner-run decomposition session.

Fills the :class:`DecompositionToolProvider` seam so the planning session can
ground its plan with live research, recall and the project's own tree instead
of reasoning purely from priors. It grants:

- ``web_search`` when a web-search provider is configured,
- a read-only ``search_memory`` tool (fusing the owner's own memory with
  company-wide org knowledge) when a memory backend is wired, so the owner can
  recall past retros and org playbooks the brief already tells it to, and
- ``read_file`` + ``list_directory`` scoped to the workspace of the project
  being planned, so a recalled claim about that project can be checked.

The last grant exists because recall spans every project the org has run while
the plan is about one of them. Without a way to look, a recalled "tetris.js
already exists" is unfalsifiable, lands in the plan's ``assumptions``, and is
rendered to the operator as settled fact: a live run planned six items to
integrate, test and deploy a game whose workspace had never been provisioned,
and planned nothing that would build it. Recall proposes; the tree disposes.

Every granted tool is read-only (``EXTERNAL_DATA_REQUEST`` / ``memory:read`` /
``code:read``), so they survive the planning session's read-only tool filter;
any future write-capable grant would be dropped there by design.
"""

from pathlib import Path

from synthorg.core.types import NotBlankStr
from synthorg.engine.workspace.paths import project_workspace_dir
from synthorg.memory.org.protocol import OrgMemoryBackend
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.recall_tool import build_memory_recall_tool
from synthorg.tools.base import BaseTool
from synthorg.tools.file_system.list_directory import ListDirectoryTool
from synthorg.tools.file_system.read_file import ReadFileTool
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
        workspace_root: Base directory holding every project's workspace, or
            ``None`` to grant no workspace reads.
    """

    __slots__ = (
        "_memory_backend",
        "_network_policy",
        "_org_backend",
        "_search_provider",
        "_workspace_root",
    )

    def __init__(
        self,
        *,
        search_provider: WebSearchProvider | None,
        memory_backend: MemoryBackend | None = None,
        org_backend: OrgMemoryBackend | None = None,
        network_policy: NetworkPolicy | None = None,
        workspace_root: Path | None = None,
    ) -> None:
        self._search_provider = search_provider
        self._memory_backend = memory_backend
        self._org_backend = org_backend
        self._network_policy = network_policy
        self._workspace_root = workspace_root

    def _workspace_tools(self, project_id: str | None) -> tuple[BaseTool, ...]:
        """Return read-only reads over *project_id*'s own workspace.

        Empty when there is no project to scope to, no configured root, or the
        project's workspace has not been provisioned yet. That last case is the
        common one and is deliberately silent: a workspace is created on first
        dispatch, so planning routinely runs before one exists, and "there is
        nothing to read" is the honest answer rather than a reason to fail the
        planning session.

        Returns:
            The scoped ``read_file`` and ``list_directory`` tools, or an empty
            tuple.
        """
        if self._workspace_root is None or project_id is None:
            return ()
        workspace = project_workspace_dir(self._workspace_root, project_id)
        if not workspace.is_dir():
            return ()
        return (
            ReadFileTool(workspace_root=workspace),
            ListDirectoryTool(workspace_root=workspace),
        )

    def build_tools(
        self,
        *,
        owner_id: str,
        project_id: str | None,
    ) -> tuple[BaseTool, ...]:
        """Return the planning tools granted to *owner_id* for *project_id*.

        Returns:
            The ``web_search`` tool when a provider is configured, a
            ``search_memory`` tool when a memory backend is wired, and reads
            scoped to *project_id*'s workspace when it has been provisioned,
            in that order.
        """
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
        tools.extend(self._workspace_tools(project_id))
        return tuple(tools)
