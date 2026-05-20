"""Agent tools for the living-documentation engine (#1976).

Exposes :class:`WriteLivingDocTool` and :class:`SearchLivingDocsTool`.
Tools delegate to :class:`DocsService`; they perform argument
validation and per-tool security classification but contain no
business logic of their own.
"""

from synthorg.tools.docs.search_living_docs import SearchLivingDocsTool
from synthorg.tools.docs.write_living_doc import WriteLivingDocTool

__all__ = ["SearchLivingDocsTool", "WriteLivingDocTool"]
