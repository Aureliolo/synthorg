"""MCP tool definition model and domain tool registry.

Provides the ``MCPToolDef`` frozen model for describing MCP tools and
``DomainToolRegistry`` for aggregating tool definitions across domains
with freeze-on-read semantics.
"""

import re
from copy import deepcopy
from types import MappingProxyType
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import (
    MCP_REGISTRY_DUPLICATE,
    MCP_REGISTRY_FROZEN,
    MCP_REGISTRY_REGISTERED,
)

logger = get_logger(__name__)


class MCPToolDef(BaseModel):
    """Immutable MCP tool definition with capability metadata.

    Attributes:
        name: Tool name following ``synthorg_{domain}_{action}`` convention.
        description: Human-readable tool description for LLM prompts.
        parameters: JSON Schema dict describing the tool's input parameters.
        capability: Capability tag in ``domain:action`` format (e.g.
            ``"tasks:read"``).  Used by ``MCPToolScoper`` for filtering.
        handler_key: Key into the handler registry for dispatch.
        args_model: Optional Pydantic model class for typed-args
            validation at the invoker boundary (#1611 Phase 4).  When
            set, :class:`MCPToolInvoker` validates the raw ``arguments``
            dict against the model before calling the handler; the
            ``ValidationError`` surfaces as an
            ``invalid_argument``-coded error envelope without ever
            reaching the handler.  ``None`` (the default) keeps the
            legacy ``common_args`` validators inside the handler body.
            Each domain's ``_*_args.py`` module exports the matching
            model alongside its tool registration.
    """

    model_config = ConfigDict(
        frozen=True, allow_inf_nan=False, arbitrary_types_allowed=True
    )

    name: NotBlankStr = Field(description="Tool name (synthorg_{domain}_{action})")
    description: NotBlankStr = Field(description="Human-readable description")
    parameters: dict[str, Any] = Field(description="JSON Schema for parameters")
    capability: NotBlankStr = Field(description="Capability tag (domain:action)")
    handler_key: NotBlankStr = Field(description="Handler registry key")
    args_model: type[BaseModel] | None = Field(
        default=None,
        description="Optional Pydantic args model for typed validation",
        exclude=True,
    )

    _NAME_RE = re.compile(r"^synthorg_[a-z][a-z0-9_]*_[a-z][a-z0-9_]*$")
    _CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$")

    @model_validator(mode="after")
    def _deepcopy_parameters(self) -> Self:
        """Deep-copy parameters at construction to prevent shared mutable state."""
        object.__setattr__(self, "parameters", deepcopy(self.parameters))
        return self

    @model_validator(mode="after")
    def _validate_name_prefix(self) -> Self:
        """Enforce ``synthorg_{domain}_{action}`` naming convention."""
        if not self._NAME_RE.match(self.name):
            msg = (
                f"Tool name must match 'synthorg_{{domain}}_{{action}}' "
                f"(lowercase alphanumeric + underscores): {self.name!r}"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_capability_format(self) -> Self:
        """Enforce ``domain:action`` capability format."""
        if not self._CAPABILITY_RE.match(self.capability):
            msg = (
                f"Capability must match 'domain:action' "
                f"(lowercase alphanumeric + underscores): {self.capability!r}"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_args_model_property_alignment(self) -> Self:
        """Catch drift between ``args_model`` and the wire JSON Schema.

        When the registration carries both an ``args_model`` (used by
        the invoker for runtime validation) and a hand-crafted
        ``parameters`` dict (the wire schema clients see in
        ``tools/list``), they MUST advertise the same property names
        AND the same required-field set.  Otherwise the invoker
        rejects shapes the wire schema accepts (or vice versa), or the
        wire schema documents one contract while validation enforces
        another -- both of which surface as silent caller bugs.
        """
        if self.args_model is None:
            return self
        wire_props = self.parameters.get("properties")
        if not isinstance(wire_props, dict):
            return self
        wire_keys = set(wire_props)
        model_keys = set(self.args_model.model_fields)
        wire_only = wire_keys - model_keys
        model_only = model_keys - wire_keys
        if wire_only or model_only:
            msg = (
                f"Tool {self.name!r} has args_model / wire-schema drift; "
                f"properties on wire but not in args_model: "
                f"{sorted(wire_only) or 'none'}; fields in args_model "
                f"but not on wire: {sorted(model_only) or 'none'}.  "
                "Either drop the explicit properties so the wire schema "
                "is derived from args_model, or update both surfaces in "
                "lockstep."
            )
            raise ValueError(msg)
        # Required-field alignment.  The wire schema's ``required``
        # list and the args_model's required-field set must agree, or
        # one side accepts payloads the other rejects.  ``FieldInfo.is_required()``
        # is True when the field has no default (positional-style required).
        wire_required_raw = self.parameters.get("required") or ()
        wire_required: set[str] = set(wire_required_raw)
        model_required = {
            field_name
            for field_name, field_info in self.args_model.model_fields.items()
            if field_info.is_required()
        }
        wire_required_only = wire_required - model_required
        model_required_only = model_required - wire_required
        if wire_required_only or model_required_only:
            msg = (
                f"Tool {self.name!r} has args_model / wire-schema "
                f"required-field drift; required on wire but optional "
                f"in args_model: {sorted(wire_required_only) or 'none'}; "
                f"required in args_model but optional on wire: "
                f"{sorted(model_required_only) or 'none'}.  Update both "
                "surfaces in lockstep."
            )
            raise ValueError(msg)
        return self


class DomainToolRegistry:
    """Central registry of all MCP tool definitions.

    Tools are registered during startup via ``register`` / ``register_many``.
    After all domains have registered, call ``freeze`` to prevent further
    mutations.  Read methods (``get``, ``get_all``, ``get_names``, etc.)
    auto-freeze on first call if not already frozen.

    Examples:
        Build and freeze a registry::

            registry = DomainToolRegistry()
            registry.register_many(TASK_TOOLS)
            registry.register_many(AGENT_TOOLS)
            registry.freeze()
            all_tools = registry.get_all()
    """

    def __init__(self) -> None:
        self._tools: dict[str, MCPToolDef] = {}
        self._frozen = False

    @property
    def frozen(self) -> bool:
        """Whether the registry has been frozen."""
        return self._frozen

    @property
    def tool_count(self) -> int:
        """Number of registered tools."""
        return len(self._tools)

    def register(self, tool: MCPToolDef) -> None:
        """Register a single tool definition.

        Args:
            tool: Tool definition to register.

        Raises:
            RuntimeError: If the registry is frozen.
            ValueError: If a tool with the same name is already registered.
        """
        if self._frozen:
            msg = "Cannot register tools after registry is frozen"
            logger.warning(
                MCP_REGISTRY_FROZEN,
                tool_name=tool.name,
                error=msg,
            )
            raise RuntimeError(msg)
        if tool.name in self._tools:
            logger.warning(
                MCP_REGISTRY_DUPLICATE,
                tool_name=tool.name,
            )
            msg = f"Duplicate tool name: {tool.name!r}"
            raise ValueError(msg)
        self._tools[tool.name] = tool
        logger.debug(
            MCP_REGISTRY_REGISTERED,
            tool_name=tool.name,
            capability=tool.capability,
        )

    def register_many(self, tools: tuple[MCPToolDef, ...]) -> None:
        """Register multiple tool definitions.

        Args:
            tools: Tuple of tool definitions to register.

        Raises:
            RuntimeError: If the registry is frozen.
            ValueError: If any tool name is duplicated.
        """
        for t in tools:
            self.register(t)

    def freeze(self) -> None:
        """Freeze the registry to prevent further mutations.

        Idempotent -- calling ``freeze`` on an already-frozen registry
        is a no-op.
        """
        if self._frozen:
            return
        self._frozen = True
        logger.debug(
            MCP_REGISTRY_FROZEN,
            tool_count=len(self._tools),
        )

    def _ensure_frozen(self) -> None:
        """Auto-freeze on first read if not already frozen."""
        if not self._frozen:
            self.freeze()

    def get(self, name: str) -> MCPToolDef:
        """Look up a tool by name.

        Returns a deep copy so callers cannot mutate registry state.

        Args:
            name: Tool name.

        Returns:
            Deep-copied tool definition.

        Raises:
            KeyError: If no tool with that name exists.
        """
        self._ensure_frozen()
        return self._tools[name].model_copy(deep=True)

    def get_all(self) -> tuple[MCPToolDef, ...]:
        """Return all registered tool definitions sorted by name.

        Returns:
            Sorted tuple of deep-copied tool definitions.
        """
        self._ensure_frozen()
        return tuple(
            t.model_copy(deep=True)
            for t in sorted(self._tools.values(), key=lambda t: t.name)
        )

    def get_tool_definitions(self) -> tuple[dict[str, Any], ...]:
        """Return all tools as plain dicts (for MCP protocol serialization).

        Each dict contains ``name``, ``description``, and ``parameters``
        keys matching the MCP tool schema format.

        Returns:
            Deep-copied tuple of tool definition dicts, sorted by name.
        """
        self._ensure_frozen()
        return tuple(
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": deepcopy(tool.parameters),
            }
            for tool in sorted(self._tools.values(), key=lambda t: t.name)
        )

    def get_names(self) -> tuple[str, ...]:
        """Return all tool names sorted alphabetically.

        Returns:
            Sorted tuple of tool name strings.
        """
        self._ensure_frozen()
        return tuple(sorted(self._tools))

    def as_mapping(self) -> MappingProxyType[str, MCPToolDef]:
        """Return a read-only mapping of deep-copied tool definitions.

        Returns:
            Frozen mapping proxy with deep-copied values.
        """
        self._ensure_frozen()
        return MappingProxyType(
            {k: v.model_copy(deep=True) for k, v in self._tools.items()}
        )
