"""Protocol definition for MCP tool handlers.

Split out from :mod:`synthorg.meta.mcp.invoker` so handler modules can
import :class:`ToolHandler` at runtime (needed by PEP 649 lazy
annotation evaluation on module-level ``MEMORY_HANDLERS: Mapping[str,
ToolHandler]`` style declarations) without pulling in the tool registry
+ provider + persistence chain that ``invoker`` drags through
``synthorg.tools.base``.  ``api.state`` is the decomposed, light
composition root (slice store plus a few primitive registries), so
naming :class:`AppState` at runtime here keeps this module light and
clear of the circular-import risk that otherwise surfaces when every
handler module tries to import ``ToolHandler`` from the invoker.
"""

from typing import Protocol

from synthorg.api.state import AppState
from synthorg.core.agent import (
    AgentIdentity,
)


class ToolHandler(Protocol):
    """Protocol for MCP tool handler functions.

    Handlers receive the application state, parsed arguments, and the
    calling actor identity (when available), returning a
    JSON-serialized string result.  The ``actor`` argument is threaded
    from the invoker so destructive-op guardrails can enforce
    attribution; handlers that don't care about identity accept it and
    ignore it.

    When the tool registration carries an ``args_model``, the invoker
    validates the raw arguments dict against the Pydantic model
    **before** calling the handler.  Failed validation surfaces as
    an ``invalid_argument`` envelope without
    invoking the handler.  Successful validation: the handler still
    receives a ``dict[str, object]`` (the validated model's
    :meth:`model_dump` output) so existing handler signatures stay
    stable; handlers that want typed access call
    ``args_model.model_validate(arguments)`` locally -- a no-op
    re-build that returns the typed model with full mypy-strict
    field access.
    """

    async def __call__(
        self,
        *,
        app_state: AppState,
        arguments: dict[str, object],
        actor: AgentIdentity | None = None,
    ) -> str:
        """Execute the tool logic.

        Args:
            app_state: Application state providing service access.
            arguments: Parsed tool arguments from the MCP call.  When
                the tool's ``args_model`` is set, this dict is the
                ``model_dump()`` of the validated typed model: every
                key matches the model's declared fields with no
                extras (because args models use ``extra="forbid"``).
            actor: Calling agent identity (typically
                ``AgentIdentity``), or ``None`` when the invoker was
                not supplied one.  Destructive-op handlers require
                non-``None``.

        Returns:
            JSON-serialized result string.
        """
        ...
