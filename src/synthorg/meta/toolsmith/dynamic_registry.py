"""Dynamic (runtime-authored) tool registry and layered read surface.

The static :class:`~synthorg.meta.mcp.registry.DomainToolRegistry` is
frozen after construction, so authored tools cannot be added to it. This
module provides:

* :func:`build_args_model` -- materialise a real frozen Pydantic args
  model from a blueprint's JSON Schema, so authored tools keep the same
  typed-validation symmetry as static tools.
* :func:`blueprint_to_mcp_def` -- promote a blueprint to an ``MCPToolDef``.
* :class:`DynamicToolRegistry` -- a mutable, snapshot-swapping registry of
  live authored tools (lock-guarded writes, lock-free atomic reads).
* :class:`LayeredToolRegistry` / :class:`LayeredHandlerMap` -- read the
  static surface first, then the dynamic layer, so the invoker dispatches
  authored tools without unfreezing anything.
"""

import asyncio
import keyword
import re
from collections.abc import Callable, Iterator, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, create_model

from synthorg.core.types import NotBlankStr
from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.registry import MCPToolDef, ToolDefReader
from synthorg.meta.toolsmith.errors import ToolRegistrationError
from synthorg.observability import get_logger
from synthorg.observability.events.toolsmith import (
    TOOLSMITH_TOOL_REGISTERED,
    TOOLSMITH_TOOL_UNREGISTERED,
)

if TYPE_CHECKING:
    from synthorg.meta.toolsmith.models import ToolBlueprint

logger = get_logger(__name__)

_JSON_TYPE_TO_PYTHON: Mapping[str, Any] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}

_MODEL_NAME_RE = re.compile(r"[^0-9a-zA-Z_]")


def _python_type_for(prop_schema: object) -> Any | None:
    """Map a JSON Schema property to a Python type for ``create_model``.

    Returns ``Any`` for an untyped property (valid JSON Schema) and the
    mapped Python type for a known type. Returns ``None`` for an explicit
    but unknown/unsupported ``type`` so the caller rejects the blueprint
    rather than silently accepting any value for that field.
    """
    if not isinstance(prop_schema, dict):
        return Any
    json_type = prop_schema.get("type")
    if json_type is None:
        return Any
    if isinstance(json_type, str):
        return _JSON_TYPE_TO_PYTHON.get(json_type)
    return None


def _model_class_name(tool_name: str) -> str:
    """Derive a valid Python class name from a tool name."""
    parts = [p for p in _MODEL_NAME_RE.sub("_", tool_name).split("_") if p]
    camel = "".join(p[:1].upper() + p[1:] for p in parts)
    candidate = f"{camel}Args"
    if not candidate[:1].isalpha() or keyword.iskeyword(candidate):
        candidate = f"Args_{candidate}"
    return candidate


def build_args_model(blueprint: ToolBlueprint) -> type[BaseModel]:
    """Materialise a frozen Pydantic args model from a blueprint schema.

    Fields mirror ``parameters_schema.properties`` exactly, with the
    ``required`` list driving which fields have no default, so the model
    aligns with the wire schema (the ``MCPToolDef`` validator enforces
    this in lockstep).

    Args:
        blueprint: The blueprint whose JSON Schema to materialise.

    Returns:
        A new frozen, ``extra="forbid"`` Pydantic model class.

    Raises:
        ToolRegistrationError: If the schema cannot be materialised.
    """
    schema = blueprint.parameters_schema
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        msg = f"blueprint {blueprint.name!r} schema lacks a properties object"
        raise ToolRegistrationError(msg)
    required_raw = schema.get("required") or ()
    required = set(required_raw) if isinstance(required_raw, (list, tuple)) else set()
    field_defs: dict[str, Any] = {}
    for prop_name, prop_schema in properties.items():
        py_type = _python_type_for(prop_schema)
        if py_type is None:
            msg = (
                f"blueprint {blueprint.name!r} property {prop_name!r} declares "
                f"an unsupported JSON Schema type"
            )
            raise ToolRegistrationError(msg)
        if prop_name in required:
            field_defs[prop_name] = (py_type, ...)
        else:
            field_defs[prop_name] = (py_type | None, None)
    # ``create_model`` raises a variety of exceptions (TypeError on bad
    # field defs, ValueError from Pydantic's own validation, etc.); wrap
    # them in the domain-specific ToolRegistrationError so callers receive
    # the contract the docstring documents.
    try:
        return create_model(
            _model_class_name(blueprint.name),
            __config__=ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False),
            **field_defs,
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        msg = (
            f"blueprint {blueprint.name!r} args model materialization failed: "
            f"{type(exc).__name__}"
        )
        raise ToolRegistrationError(msg) from exc


def blueprint_to_mcp_def(blueprint: ToolBlueprint) -> MCPToolDef:
    """Promote a blueprint to an ``MCPToolDef`` with a materialised args model.

    The handler key is the tool name itself (unique per authored tool),
    so the layered handler map can resolve the per-tool closure.

    Raises:
        ToolRegistrationError: If the schema cannot be materialised or the
            resulting definition violates the MCP tool contract.
    """
    args_model = build_args_model(blueprint)
    try:
        return MCPToolDef(
            name=blueprint.name,
            description=blueprint.description,
            parameters=blueprint.parameters_schema,
            capability=blueprint.capability,
            handler_key=blueprint.name,
            args_model=args_model,
        )
    except ValueError as exc:
        msg = f"blueprint {blueprint.name!r} is not a valid MCP tool: {exc}"
        raise ToolRegistrationError(msg) from exc


class _Entry:
    """A live authored tool: its definition plus its handler closure."""

    __slots__ = ("definition", "handler")

    def __init__(self, definition: MCPToolDef, handler: ToolHandler) -> None:
        self.definition = definition
        self.handler = handler


class DynamicToolRegistry:
    """Mutable registry of live authored tools (snapshot-swapping reads).

    Writes are serialised under an :class:`asyncio.Lock`; each mutation
    rebuilds an immutable snapshot dict and swaps the reference, so the
    synchronous read paths (used by the invoker mid-dispatch) observe a
    consistent view without locking. In the single-threaded event loop a
    sync read cannot interleave with an async write between awaits.

    Args:
        handler_factory: Builds the per-tool :class:`ToolHandler` closure
            for a blueprint (binds the sandbox + repository). Injected so
            the registry stays testable without a real sandbox.
    """

    def __init__(
        self,
        *,
        handler_factory: Callable[[ToolBlueprint], ToolHandler],
    ) -> None:
        self._handler_factory = handler_factory
        self._lock = asyncio.Lock()
        self._snapshot: Mapping[str, _Entry] = MappingProxyType({})

    async def register(self, blueprint: ToolBlueprint) -> None:
        """Register an active blueprint as a live MCP tool.

        Idempotent on the tool name: re-registering replaces the entry.

        Raises:
            ToolRegistrationError: If the blueprint cannot be promoted.
        """
        definition = blueprint_to_mcp_def(blueprint)
        handler = self._handler_factory(blueprint)
        async with self._lock:
            merged = dict(self._snapshot)
            merged[blueprint.name] = _Entry(definition, handler)
            self._snapshot = MappingProxyType(merged)
        logger.info(
            TOOLSMITH_TOOL_REGISTERED,
            tool_name=blueprint.name,
            capability=blueprint.capability,
        )

    async def unregister(self, name: NotBlankStr) -> bool:
        """Remove a live tool by name; ``True`` iff it was present."""
        async with self._lock:
            if name not in self._snapshot:
                return False
            merged = dict(self._snapshot)
            del merged[name]
            self._snapshot = MappingProxyType(merged)
        logger.info(TOOLSMITH_TOOL_UNREGISTERED, tool_name=name)
        return True

    def names(self) -> tuple[NotBlankStr, ...]:
        """Return the currently-registered dynamic tool names (sorted)."""
        return tuple(NotBlankStr(n) for n in sorted(self._snapshot))

    def capabilities(self) -> tuple[NotBlankStr, ...]:
        """Return the capability tags of currently-registered dynamic tools.

        The toolsmith generator consumes this as a dedup hint so authored
        tools never duplicate a capability already live in the dynamic
        layer of the same process.
        """
        return tuple(
            NotBlankStr(entry.definition.capability)
            for entry in (self._snapshot[n] for n in sorted(self._snapshot))
        )

    def get_def(self, name: str) -> MCPToolDef | None:
        """Return the definition for ``name``, or ``None`` if absent."""
        entry = self._snapshot.get(name)
        return entry.definition if entry is not None else None

    def get_handler(self, handler_key: str) -> ToolHandler | None:
        """Return the handler for ``handler_key``, or ``None`` if absent."""
        entry = self._snapshot.get(handler_key)
        return entry.handler if entry is not None else None


class LayeredToolRegistry:
    """Static + dynamic tool-definition read surface (``ToolDefReader``).

    Reads the frozen static registry first, then the dynamic layer, so
    authored tools resolve without unfreezing the static surface.
    """

    def __init__(
        self,
        static_registry: ToolDefReader,
        dynamic_registry: DynamicToolRegistry,
    ) -> None:
        self._static = static_registry
        self._dynamic = dynamic_registry

    def get(self, name: str) -> MCPToolDef:
        """Look up a tool by name, static layer first.

        Raises:
            KeyError: If neither layer knows the name.
        """
        try:
            return self._static.get(name)
        except KeyError:
            dynamic = self._dynamic.get_def(name)
            if dynamic is None:
                raise
            return dynamic

    def get_names(self) -> tuple[str, ...]:
        """Return all known tool names (static + dynamic), sorted."""
        return tuple(sorted({*self._static.get_names(), *self._dynamic.names()}))


class LayeredHandlerMap(Mapping[str, ToolHandler]):
    """Static + dynamic handler map; static keys win on collision."""

    def __init__(
        self,
        static_handlers: Mapping[str, ToolHandler],
        dynamic_registry: DynamicToolRegistry,
    ) -> None:
        self._static = static_handlers
        self._dynamic = dynamic_registry

    def __getitem__(self, key: str) -> ToolHandler:
        """Resolve a handler, static layer first then dynamic."""
        if key in self._static:
            return self._static[key]
        handler = self._dynamic.get_handler(key)
        if handler is None:
            raise KeyError(key)
        return handler

    def __iter__(self) -> Iterator[str]:
        """Iterate static handler keys then dynamic tool names."""
        seen = set(self._static)
        yield from self._static
        for name in self._dynamic.names():
            if name not in seen:
                yield name

    def __len__(self) -> int:
        """Return the count of distinct static + dynamic keys."""
        return len(set(self._static) | set(self._dynamic.names()))


__all__ = [
    "DynamicToolRegistry",
    "LayeredHandlerMap",
    "LayeredToolRegistry",
    "blueprint_to_mcp_def",
    "build_args_model",
]
