"""Typed argument models for the six self-editing-memory tools.

Each tool dispatched by
:meth:`synthorg.memory.self_editing.SelfEditingMemoryStrategy.handle_tool_call`
has a frozen Pydantic model carrying a ``tool`` ``Literal`` discriminator.
:data:`SelfEditingArgs` is the discriminated union the dispatcher uses
to validate the LLM-supplied ``arguments`` dict before calling the
matching handler -- the manual ``arguments.get(...)`` walks and
``_extract_str`` helper at the dispatch boundary go away.

The models only enforce static shape (required keys, types, length
caps).  Config-driven runtime checks (``allow_core_writes``,
``core_max_entries``, ``archival_categories`` allowlist,
``archival_search_limit`` clamp) stay inside the handlers because they
depend on per-agent state the model does not know about.
"""

from typing import Annotated, Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    TypeAdapter,
)

from synthorg.core.enums import MemoryCategory
from synthorg.core.types import NotBlankStr

# Persistent (non-volatile) subset of MemoryCategory.  Archival memory
# only stores content that survives the session; WORKING is the
# session-scoped tier and must not reach archival handlers.  Narrowing
# at the args boundary blocks the invalid value at parse time so
# ``_handle_archival_memory_search`` and ``_handle_archival_memory_write``
# never receive WORKING.
PersistentArchivalCategory = Literal[
    MemoryCategory.EPISODIC,
    MemoryCategory.SEMANTIC,
    MemoryCategory.PROCEDURAL,
    MemoryCategory.SOCIAL,
]

_MAX_CONTENT_LEN: Final[int] = 50_000
_MAX_MEMORY_ID_LEN: Final[int] = 256
# Generous belt-and-braces ceiling on archival search limit.  The
# handler clamps to ``[1, config.archival_search_limit]`` -- typically
# much smaller -- but the model rejects pathological values like
# ``limit=1_000_000`` that would otherwise bypass per-config clamps in
# unwired code paths.
_MAX_ARCHIVAL_SEARCH_LIMIT: Final[int] = 1_000


_ARGS_CONFIG = ConfigDict(
    frozen=True,
    allow_inf_nan=False,
    extra="forbid",
)


class CoreMemoryReadArgs(BaseModel):
    """Args for ``core_memory_read``: no fields beyond the discriminator."""

    model_config = _ARGS_CONFIG

    tool: Literal["core_memory_read"] = "core_memory_read"


class CoreMemoryWriteArgs(BaseModel):
    """Args for ``core_memory_write``."""

    model_config = _ARGS_CONFIG

    tool: Literal["core_memory_write"] = "core_memory_write"
    content: NotBlankStr = Field(
        max_length=_MAX_CONTENT_LEN,
        description="Text to store in core memory",
    )


class ArchivalMemorySearchArgs(BaseModel):
    """Args for ``archival_memory_search``.

    ``limit`` is bounded by a generous ceiling here; the handler still
    clamps to ``[1, config.archival_search_limit]`` at runtime so the
    operational cap can move with config.  The model ceiling is a
    belt-and-braces guard against pathological values reaching the
    handler in unwired code paths.
    """

    model_config = _ARGS_CONFIG

    tool: Literal["archival_memory_search"] = "archival_memory_search"
    query: NotBlankStr = Field(description="Natural language search query")
    category: PersistentArchivalCategory | None = Field(
        default=None,
        description="Optional category filter (excludes WORKING)",
    )
    limit: int | None = Field(
        default=None,
        gt=0,
        le=_MAX_ARCHIVAL_SEARCH_LIMIT,
        description="Maximum results to return",
    )


class ArchivalMemoryWriteArgs(BaseModel):
    """Args for ``archival_memory_write``.

    The category-allowlist check (``category in
    config.archival_categories``) stays in the handler because the
    allowlist is config-driven, not a static enum subset.
    """

    model_config = _ARGS_CONFIG

    tool: Literal["archival_memory_write"] = "archival_memory_write"
    content: NotBlankStr = Field(
        max_length=_MAX_CONTENT_LEN,
        description="Text to store in archival memory",
    )
    category: PersistentArchivalCategory = Field(
        description="Memory category (excludes WORKING)",
    )


class RecallMemoryReadArgs(BaseModel):
    """Args for ``recall_memory_read``."""

    model_config = _ARGS_CONFIG

    tool: Literal["recall_memory_read"] = "recall_memory_read"
    memory_id: NotBlankStr = Field(
        max_length=_MAX_MEMORY_ID_LEN,
        description="Exact memory ID to retrieve",
    )


class RecallMemoryWriteArgs(BaseModel):
    """Args for ``recall_memory_write``."""

    model_config = _ARGS_CONFIG

    tool: Literal["recall_memory_write"] = "recall_memory_write"
    content: NotBlankStr = Field(
        max_length=_MAX_CONTENT_LEN,
        description="Episodic event or experience to record",
    )


SelfEditingArgs = Annotated[
    CoreMemoryReadArgs
    | CoreMemoryWriteArgs
    | ArchivalMemorySearchArgs
    | ArchivalMemoryWriteArgs
    | RecallMemoryReadArgs
    | RecallMemoryWriteArgs,
    Discriminator("tool"),
]
"""Discriminated union of typed self-editing-memory tool args.

Pydantic uses the ``tool`` literal on each variant to deserialize into
the correct typed model.
"""


_ARGS_ADAPTER: TypeAdapter[SelfEditingArgs] = TypeAdapter(SelfEditingArgs)


def parse_self_editing_args(
    tool_name: str,
    arguments: dict[str, object],
) -> SelfEditingArgs:
    """Parse raw LLM-supplied arguments into the matching typed variant.

    The tool name from the dispatch envelope overrides any ``tool`` key
    inside ``arguments``: an LLM that smuggles ``arguments={"tool":
    "core_memory_read", ...}`` while the dispatcher saw a different
    tool name is rejected by the discriminator.

    Args:
        tool_name: The dispatched tool name (e.g. ``"core_memory_write"``).
        arguments: Raw LLM-supplied argument dict.

    Returns:
        One of the six ``*Args`` variants chosen by ``tool_name``.

    Raises:
        ValidationError: When the arguments do not match the chosen
            variant's shape (missing keys, wrong types, blank strings,
            oversized values, unknown enum members).
    """
    payload: dict[str, object] = {**arguments, "tool": tool_name}
    return _ARGS_ADAPTER.validate_python(payload)


__all__ = [
    "ArchivalMemorySearchArgs",
    "ArchivalMemoryWriteArgs",
    "CoreMemoryReadArgs",
    "CoreMemoryWriteArgs",
    "RecallMemoryReadArgs",
    "RecallMemoryWriteArgs",
    "SelfEditingArgs",
    "parse_self_editing_args",
]
