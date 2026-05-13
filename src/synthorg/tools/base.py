"""Base tool abstraction and execution result model.

Defines the ``BaseTool`` ABC that all concrete tools extend, and the
``ToolExecutionResult`` value object returned by tool execution.
"""

import copy
from abc import ABC, abstractmethod
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from synthorg.observability import get_logger

if TYPE_CHECKING:
    from synthorg.core.enums import ToolCategory
from synthorg.core.tool_disclosure import ToolL1Metadata, ToolL2Body, ToolL3Resource
from synthorg.observability.events.tool import TOOL_BASE_INVALID_NAME
from synthorg.providers.models import ToolDefinition
from synthorg.security.action_type_mapping import DEFAULT_CATEGORY_ACTION_MAP

logger = get_logger(__name__)


class ToolExecutionResult(BaseModel):
    """Result of executing a tool's business logic.

    This is the internal result type returned by ``BaseTool.execute``.
    The invoker converts it into a ``ToolResult`` for the LLM, carrying
    only ``content`` and ``is_error`` -- ``metadata`` is not forwarded
    to the LLM and is available only for programmatic consumers.

    Note:
        The ``metadata`` dict is shallowly frozen by Pydantic's
        ``frozen=True``.  Tool implementations construct and return
        this model, but the invoker converts it into a provider-facing
        ``ToolResult`` -- ``metadata`` is not forwarded to LLM providers
        or other external boundaries, so no additional boundary copy
        is needed at this layer.

    Attributes:
        content: Tool output as a string.
        is_error: Whether the execution failed.
        metadata: Optional structured data for programmatic consumers.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    content: str = Field(description="Tool output")
    is_error: bool = Field(default=False, description="Whether tool errored")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional structured metadata",
    )


class BaseTool(ABC):
    """Abstract base class for all tools in the system.

    Subclasses must implement ``execute`` to define tool behavior.
    The ``to_definition`` method converts the tool into a
    ``ToolDefinition`` suitable for sending to an LLM provider.

    Attributes:
        name: Non-blank tool name.
        description: Human-readable description of the tool.
        parameters_schema: JSON Schema dict describing expected arguments,
            or ``None`` if no parameter schema is defined (the invoker
            skips validation).
        category: Tool category for access-level gating.
        action_type: Security action type for SecOps classification.
        args_model: Optional Pydantic model class for typed argument
            validation.  When set on a subclass, the
            ``ToolInvoker`` validates raw arguments against the model
            before calling :meth:`execute`; the ``Args.model_validate``
            ``ValidationError`` surfaces as a parameter-error
            ``ToolResult`` without ever reaching the tool body.
            Each ``synthorg.tools.<domain>._args`` module exports the
            matching model alongside the tool subclass.  Defaults to
            ``None`` (the invoker falls back to JSON-Schema validation
            via :attr:`parameters_schema`); subclasses migrated to
            typed args set this to the relevant model class.
    """

    args_model: ClassVar[type[BaseModel] | None] = None

    def __init__(
        self,
        *,
        name: str,
        description: str = "",
        parameters_schema: dict[str, Any] | None = None,
        category: ToolCategory,
        action_type: str | None = None,
    ) -> None:
        """Initialize a tool with name, description, schema, and category.

        Args:
            name: Non-blank tool name.
            description: Human-readable description.
            parameters_schema: JSON Schema for tool parameters.
            category: Tool category for access-level gating.
            action_type: Security action type for SecOps classification.
                When ``None``, derived from the category via
                ``DEFAULT_CATEGORY_ACTION_MAP``.

        Raises:
            ValueError: If name is empty or whitespace-only.
        """
        if not name or not name.strip():
            logger.warning(TOOL_BASE_INVALID_NAME, name=repr(name))
            msg = "Tool name must not be empty or whitespace-only"
            raise ValueError(msg)
        self._name = name
        self._description = description
        self._category = category
        if action_type is not None:
            parts = action_type.split(":")
            if len(parts) != 2 or not parts[0] or not parts[1]:  # noqa: PLR2004
                msg = f"action_type {action_type!r} must use 'category:action' format"
                logger.warning(TOOL_BASE_INVALID_NAME, name=repr(action_type))
                raise ValueError(msg)
            self._action_type = action_type
        else:
            if category not in DEFAULT_CATEGORY_ACTION_MAP:
                msg = f"No default action_type mapping for ToolCategory.{category.name}"
                raise ValueError(msg)
            self._action_type = str(DEFAULT_CATEGORY_ACTION_MAP[category])
        if parameters_schema is None:
            self._parameters_schema: MappingProxyType[str, Any] | None = None
        else:
            # Build the typed model FIRST so a non-JSON value (set,
            # custom class, ...) fails fast at construction instead
            # of corrupting downstream JSON emission.
            from synthorg.tools.parameters_schema import (  # noqa: PLC0415
                ToolParametersSchema,
            )

            validated = ToolParametersSchema.model_validate(parameters_schema)
            self._parameters_schema = MappingProxyType(validated.as_dict())

    @property
    def name(self) -> str:
        """Tool name."""
        return self._name

    @property
    def category(self) -> ToolCategory:
        """Tool category for access-level gating."""
        return self._category

    @property
    def action_type(self) -> str:
        """Security action type for SecOps classification."""
        return self._action_type

    @property
    def description(self) -> str:
        """Tool description."""
        return self._description

    @property
    def parameters_schema(self) -> dict[str, Any] | None:
        """JSON Schema for tool parameters, or None if unspecified.

        Returns a deep copy to prevent mutation of internal state.
        """
        if self._parameters_schema is None:
            return None
        # dict() needed: MappingProxyType cannot be deep-copied directly
        return copy.deepcopy(dict(self._parameters_schema))

    def to_l1_metadata(self) -> ToolL1Metadata:
        """Return L1 always-in-context summary.

        Default implementation derives metadata from existing
        ``name``, ``description``, and ``category`` fields.
        Override in subclasses for richer metadata.

        Returns:
            L1 metadata with name, short description, category,
            and ``"medium"`` cost tier.
        """
        return ToolL1Metadata(
            name=self._name,
            short_description=self._description[:200],
            category=self._category.value,
            typical_cost_tier="medium",
        )

    def to_l2_body(self) -> ToolL2Body:
        """Return L2 on-demand instruction body.

        Default implementation derives from existing ``description``
        and ``parameters_schema``.  Override in subclasses for richer
        content (usage examples, failure modes).

        Returns:
            L2 body with full description, parameter schema, and
            empty examples/failure modes.
        """
        return ToolL2Body(
            full_description=self._description,
            parameter_schema=self.parameters_schema or {},
            usage_examples=(),
            failure_modes=(),
        )

    def get_l3_resources(self) -> tuple[ToolL3Resource, ...]:
        """Return L3 explicit-request resources.

        Default implementation returns no resources.  Override in
        subclasses to provide advanced documentation, code samples,
        or example traces.

        Returns:
            Empty tuple (no resources by default).
        """
        return ()

    def to_definition(self) -> ToolDefinition:
        """Convert this tool to a ``ToolDefinition`` for LLM providers.

        Populates both the flat provider-facing fields (``name``,
        ``description``, ``parameters_schema``) and the tiered
        disclosure fields (``l1_metadata``, ``l2_body``,
        ``l3_resources``).

        Returns:
            A ``ToolDefinition`` with all fields populated.
        """
        return ToolDefinition(
            name=self._name,
            description=self._description,
            parameters_schema=self.parameters_schema or {},
            l1_metadata=self.to_l1_metadata(),
            l2_body=self.to_l2_body(),
            l3_resources=self.get_l3_resources(),
        )

    @abstractmethod
    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Execute the tool with the given arguments.

        Arguments are pre-validated against the tool's JSON Schema (if
        one is defined) by the ``ToolInvoker`` before reaching this
        method.  Implementations with a schema can assume compliance
        when invoked through the invoker; tools without a schema
        receive unvalidated arguments.

        Args:
            arguments: Parsed arguments matching the parameters schema.

        Returns:
            A ``ToolExecutionResult`` with the tool output.
        """
