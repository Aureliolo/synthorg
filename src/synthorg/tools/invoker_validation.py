"""Parameter-validation helpers for ``ToolInvoker``.

Owns ``_validate_params``, ``_schema_error_result``,
``_param_error_result``, ``_unexpected_validation_result``, and
``_safe_deepcopy_args``.  These are pure helpers that don't depend
on invoker state beyond the tool/arguments being validated.
"""

import copy
from typing import Never

import jsonschema
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from referencing import Registry as JsonSchemaRegistry
from referencing.exceptions import NoSuchResource

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.tool import (
    TOOL_INVOKE_DEEPCOPY_ERROR,
    TOOL_INVOKE_NON_RECOVERABLE,
    TOOL_INVOKE_PARAMETER_ERROR,
    TOOL_INVOKE_SCHEMA_ERROR,
    TOOL_INVOKE_VALIDATION_UNEXPECTED,
)
from synthorg.providers.models import ToolCall, ToolResult
from synthorg.tools.base import BaseTool
from synthorg.tools.errors import ToolParameterError

logger = get_logger(__name__)


def _no_remote_retrieve(uri: str) -> Never:
    """Block remote ``$ref`` resolution to prevent SSRF.

    Raises:
        NoSuchResource: Raised when the relevant invariant fails.
    """
    raise NoSuchResource(uri)


SAFE_REGISTRY = JsonSchemaRegistry(  # type: ignore[call-arg]
    retrieve=_no_remote_retrieve,
)


def _format_pydantic_error(err: object) -> str:
    """Render a single Pydantic ``errors()`` entry as ``loc: msg``.

    Returns:
        Result of type ``str``.
    """
    if not isinstance(err, dict):
        return "<arguments>: invalid"
    loc_raw = err.get("loc", ())
    loc_parts = loc_raw if isinstance(loc_raw, tuple) else ()
    loc = ".".join(str(p) for p in loc_parts) or "<arguments>"
    msg = err.get("msg", "")
    return f"{loc}: {msg}" if isinstance(msg, str) else f"{loc}: invalid"


class ToolInvokerValidationMixin:
    """Parameter-validation helpers for ``ToolInvoker``."""

    def _validate_params(
        self,
        tool: BaseTool,
        tool_call: ToolCall,
    ) -> ToolResult | dict[str, object] | None:
        """Validate tool call arguments.

        Tries the typed ``args_model`` when the subclass declares one
        and falls back to JSON-Schema validation against
        ``parameters_schema`` otherwise.

        Returns:
          * ``ToolResult`` on validation failure (caller short-circuits).
          * ``dict[str, object]`` (the normalized args-model dump) when
            an ``args_model`` validated successfully.  Defaults,
            coercions, and ``AfterValidator`` results are baked in;
            callers MUST pass this dict to ``tool.execute`` instead of
            the raw ``tool_call.arguments`` so the typed-boundary
            promise actually reaches the tool body.
          * ``None`` when there is no ``args_model`` and the legacy
            JSON-Schema check passed; callers fall back to the raw
            deepcopied arguments.
        """
        args_model = tool.args_model
        if args_model is not None:
            return self._validate_args_model(args_model, tool_call)
        return self._validate_json_schema(tool, tool_call)

    def _validate_args_model(
        self,
        args_model: type[BaseModel],
        tool_call: ToolCall,
    ) -> ToolResult | dict[str, object]:
        """Validate ``tool_call.arguments`` against a Pydantic args model.

        Returns the validated ``model_dump(mode="python")`` on success
        so coercions / defaults / ``AfterValidator`` results propagate
        to the tool body (per the typed-args contract); returns a
        ``ToolResult`` on failure.

        Returns:
            Result of type ``ToolResult | dict[str, object]``.

        Raises:
            MemoryError: If the related operation fails.
            RecursionError: If the related operation fails.
        """
        try:
            validated = args_model.model_validate(dict(tool_call.arguments))
            # ``model_dump`` is inside the same ``try`` so a
            # serialization failure (custom serializer raising,
            # unbounded recursion in nested types, etc.) flows through
            # the same failure paths as validation -- without this,
            # serialization errors would escape ``_validate_args_model``
            # uncaught and break the method's "always returns a
            # ToolResult or a normalized dict" contract.
            return validated.model_dump(mode="python")
        except PydanticValidationError as exc:
            errors = exc.errors(include_input=False, include_url=False)
            detail = (
                "; ".join(_format_pydantic_error(e) for e in errors)
                if errors
                else safe_error_description(exc)
            )
            return self._param_error_result(tool_call, detail)
        except (MemoryError, RecursionError) as exc:
            log_exception_redacted(
                logger,
                TOOL_INVOKE_NON_RECOVERABLE,
                exc,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
            )
            raise
        except Exception as exc:
            reraise_critical(exc)
            return self._unexpected_validation_result(
                tool_call, safe_error_description(exc) or type(exc).__name__
            )

    def _validate_json_schema(
        self,
        tool: BaseTool,
        tool_call: ToolCall,
    ) -> ToolResult | None:
        """Validate ``tool_call.arguments`` against the legacy JSON Schema.

        Returns:
            The resulting ``ToolResult``, or ``None`` when unavailable.

        Raises:
            MemoryError: If the related operation fails.
            RecursionError: If the related operation fails.
        """
        schema = tool.parameters_schema
        if schema is None:
            return None
        try:
            jsonschema.validate(
                instance=dict(tool_call.arguments),
                schema=schema,
                registry=SAFE_REGISTRY,
            )
        except jsonschema.SchemaError as exc:
            return self._schema_error_result(tool_call, exc.message)
        except jsonschema.ValidationError as exc:
            return self._param_error_result(tool_call, exc.message)
        except (MemoryError, RecursionError) as exc:
            log_exception_redacted(
                logger,
                TOOL_INVOKE_NON_RECOVERABLE,
                exc,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
            )
            raise
        except Exception as exc:
            reraise_critical(exc)
            return self._unexpected_validation_result(
                tool_call, safe_error_description(exc) or type(exc).__name__
            )
        return None

    def _schema_error_result(
        self,
        tool_call: ToolCall,
        error_msg: str,
    ) -> ToolResult:
        """Build an error result for an invalid tool schema.

        Returns:
            Result of type ``ToolResult``.
        """
        logger.error(
            TOOL_INVOKE_SCHEMA_ERROR,
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            error=error_msg,
        )
        return ToolResult(
            tool_call_id=tool_call.id,
            content=(
                f"Tool {tool_call.name!r} has an invalid parameter schema: {error_msg}"
            ),
            is_error=True,
        )

    def _param_error_result(
        self,
        tool_call: ToolCall,
        error_msg: str,
    ) -> ToolResult:
        """Build an error result for failed parameter validation.

        Returns:
            Result of type ``ToolResult``.
        """
        logger.warning(
            TOOL_INVOKE_PARAMETER_ERROR,
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            error=error_msg,
        )
        param_err = ToolParameterError(
            error_msg,
            context={"tool": tool_call.name},
        )
        return ToolResult(
            tool_call_id=tool_call.id,
            content=str(param_err),
            is_error=True,
        )

    def _unexpected_validation_result(
        self,
        tool_call: ToolCall,
        error_msg: str,
    ) -> ToolResult:
        """Build an error result for unexpected validation failures.

        Returns:
            Result of type ``ToolResult``.
        """
        logger.warning(
            TOOL_INVOKE_VALIDATION_UNEXPECTED,
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            error=error_msg,
        )
        return ToolResult(
            tool_call_id=tool_call.id,
            content=(
                f"Tool {tool_call.name!r} parameter validation failed: {error_msg}"
            ),
            is_error=True,
        )

    def _safe_deepcopy_args(
        self,
        tool_call: ToolCall,
    ) -> dict[str, object] | ToolResult:
        """Deep-copy tool call arguments for isolation.

        Returns the copied dict on success, or a ``ToolResult`` on
        failure.  Non-recoverable errors propagate after logging.

        Returns:
            Result of type ``dict[str, object] | ToolResult``.

        Raises:
            MemoryError: If the related operation fails.
            RecursionError: If the related operation fails.
        """
        try:
            # Widen the parsed-LLM ``JsonValue`` arguments to ``object`` at
            # this isolation boundary: the deep copy is what tool code mutates.
            isolated: dict[str, object] = {**copy.deepcopy(tool_call.arguments)}
        except (MemoryError, RecursionError) as exc:
            log_exception_redacted(
                logger,
                TOOL_INVOKE_NON_RECOVERABLE,
                exc,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
            )
            raise
        except Exception as exc:
            reraise_critical(exc)
            error_msg = safe_error_description(exc) or type(exc).__name__
            logger.warning(
                TOOL_INVOKE_DEEPCOPY_ERROR,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                error_type=type(exc).__name__,
                error=error_msg,
            )
            return ToolResult(
                tool_call_id=tool_call.id,
                content=(
                    f"Tool {tool_call.name!r} arguments could not be "
                    f"safely copied: {error_msg}"
                ),
                is_error=True,
            )
        else:
            return isolated
