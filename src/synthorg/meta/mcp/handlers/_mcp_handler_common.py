"""Argument + serialisation helpers shared across MCP handler modules.

The capability-gap envelope mapper, the required non-blank-string and
required UUID-string extractors, and the JSON-coercion helper -- the
subset that is byte-identical between the organization and integrations
handler helper modules. Domain-specific extractors (optional / list /
int variants) stay in their respective ``_*_helpers`` modules.
"""

from uuid import UUID

from pydantic import BaseModel, ValidationError

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.core.types import NotBlankStr
from synthorg.meta.mcp.errors import ArgumentValidationError
from synthorg.meta.mcp.handlers.common import err
from synthorg.meta.mcp.handlers.common_args import get_optional_str, require_arg
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_HANDLER_CAPABILITY_GAP

logger = get_logger(__name__)

_TY_STRING = "non-blank string"
_TY_UUID = "UUID string"
_ARG_ARGUMENTS = "arguments"


def typed_args[ArgsT: BaseModel](
    arguments: dict[str, object],
    model: type[ArgsT],
) -> ArgsT:
    """Validate a raw MCP argument dict into its typed args model.

    The MCP invoker has already validated the raw dict against the wired
    ``args_model`` and dumped it back to a plain dict; this is the no-op
    re-build documented by the ``ToolHandler`` protocol that restores
    mypy-strict typed field access inside the handler body.

    Returns:
        ``ArgsT`` instance.

    Raises:
        ArgumentValidationError: When the dict does not validate (only
            reachable if a handler is called outside the invoker).
    """
    try:
        return model.model_validate(arguments)
    except ValidationError as exc:
        expected = f"valid {model.__name__}"
        raise ArgumentValidationError(_ARG_ARGUMENTS, expected) from exc


def _map_capability(tool: str, exc: CapabilityNotSupportedError) -> str:
    """Translate a facade-side capability gap into a typed error envelope.

    Emits :data:`MCP_HANDLER_CAPABILITY_GAP` so capability telemetry is
    distinct from invoke failures.

    Returns:
        Resulting string.
    """
    logger.info(
        MCP_HANDLER_CAPABILITY_GAP,
        tool_name=tool,
        capability=exc.capability,
    )
    return err(exc, domain_code=exc.domain_code)


def _require_str(arguments: dict[str, object], key: str) -> NotBlankStr:
    """Extract a required non-blank string or raise ``ArgumentValidationError``.

    Returns:
        ``NotBlankStr`` instance.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    value = get_optional_str(arguments, key)
    if value is None:
        raise ArgumentValidationError(key, _TY_STRING)
    return value


def _require_uuid(arguments: dict[str, object], key: str) -> NotBlankStr:
    """Extract a required UUID-shaped string or raise ``ArgumentValidationError``.

    Returns:
        ``NotBlankStr`` instance.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    value = require_arg(arguments, key, str)
    try:
        UUID(value)
    except ValueError as exc:
        raise ArgumentValidationError(key, _TY_UUID) from exc
    return NotBlankStr(value)


def _to_jsonable(value: object) -> object:
    """Coerce a Pydantic / ``to_dict`` value into a JSON-serialisable form.

    Returns:
        JSON-serialisable representation of ``value``.
    """
    dump_fn = getattr(value, "model_dump", None)
    if callable(dump_fn):
        return dump_fn(mode="json")
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return value
