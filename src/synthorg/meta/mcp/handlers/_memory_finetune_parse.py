# module-kind: code
"""Stateless argument parsing for the memory fine-tune MCP tools.

Turns the loosely-typed MCP ``arguments`` dict into a validated
:class:`FineTunePlan`, raising :class:`ArgumentValidationError` with the
offending field name so the handler can return an ``invalid_argument``
envelope. Carries no handler or service state.
"""

from typing import Any

from synthorg.core.types import NotBlankStr
from synthorg.memory.embedding.fine_tune_models import FineTuneExecutionConfig
from synthorg.memory.fine_tune_plan import FineTunePlan
from synthorg.meta.mcp.errors import ArgumentValidationError
from synthorg.meta.mcp.handlers.common_args import require_non_blank

_TY_NON_BLANK = "non-blank string"
_OPTIONAL_STR_KEYS: tuple[str, ...] = (
    "base_model",
    "output_dir",
    "resume_run_id",
)
_OPTIONAL_INT_KEYS: tuple[str, ...] = ("epochs", "top_k", "batch_size")
_OPTIONAL_FLOAT_KEYS: tuple[str, ...] = (
    "learning_rate",
    "temperature",
    "validation_split",
)
_ARG_EXECUTION = "execution"
_TY_EXECUTION_OR_NULL = "object or null"
_TY_EXECUTION_SHAPE = "valid FineTuneExecutionConfig shape"


def _collect_optional_strings(
    arguments: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    """Handle collect optional strings.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    for key in _OPTIONAL_STR_KEYS:
        if key not in arguments:
            continue
        raw = arguments[key]
        if raw is None:
            continue
        if not isinstance(raw, str) or not raw.strip():
            # Reject present-but-malformed values (e.g. ``""`` or a
            # non-string) instead of silently dropping them -- otherwise
            # ``resume_run_id=""`` would become a fresh fine-tune rather
            # than an ``invalid_argument`` response.
            raise ArgumentValidationError(key, _TY_NON_BLANK)
        payload[key] = NotBlankStr(raw.strip())


def _collect_optional_ints(
    arguments: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    """Handle collect optional ints.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    for key in _OPTIONAL_INT_KEYS:
        raw = arguments.get(key)
        if raw is None:
            continue
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ArgumentValidationError(key, "positive int")
        payload[key] = raw


def _collect_optional_floats(
    arguments: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    """Handle collect optional floats.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    for key in _OPTIONAL_FLOAT_KEYS:
        raw = arguments.get(key)
        if raw is None:
            continue
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ArgumentValidationError(key, "positive float")
        payload[key] = float(raw)


def _collect_optional_execution(
    arguments: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    """Translate the optional ``execution`` object into a typed sub-model.

    Silently dropping the field would let callers send runner / backend
    overrides and still get ``"status": "ok"`` back while the service
    runs with defaults -- a hard-to-debug contract hole. Instead, any
    present-but-not-null value must be a JSON object that validates
    against :class:`FineTuneExecutionConfig`, else we return an
    ``invalid_argument`` envelope with the nested field name.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    raw = arguments.get(_ARG_EXECUTION)
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise ArgumentValidationError(_ARG_EXECUTION, _TY_EXECUTION_OR_NULL)
    try:
        payload[_ARG_EXECUTION] = FineTuneExecutionConfig(**raw)
    except Exception as exc:
        raise ArgumentValidationError(_ARG_EXECUTION, _TY_EXECUTION_SHAPE) from exc


def parse_fine_tune_plan(arguments: dict[str, Any]) -> FineTunePlan:
    """Build a :class:`FineTunePlan` from MCP arguments with typed errors.

    Returns:
        ``FineTunePlan`` instance.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    source_dir = require_non_blank(arguments, "source_dir")
    payload: dict[str, Any] = {"source_dir": NotBlankStr(source_dir)}
    _collect_optional_strings(arguments, payload)
    _collect_optional_ints(arguments, payload)
    _collect_optional_floats(arguments, payload)
    _collect_optional_execution(arguments, payload)
    try:
        return FineTunePlan(**payload)
    except Exception as exc:
        arg_name = "plan"
        expected = "valid FineTunePlan shape"
        raise ArgumentValidationError(arg_name, expected) from exc
