"""Shared base arg fragments for MCP domain typed-args models.

Houses the cross-cutting shapes the existing
``synthorg.meta.mcp.tool_builder`` constants encode as JSON Schema:

* ``PaginationFields`` -- the ``offset`` / ``limit`` pair every
  ``read_tool`` paginated list shares.
* ``AdminGuardrailFields`` -- the ``confirm: Literal[True]`` /
  ``reason: NotBlankStr`` pair every ``admin_tool`` destructive op
  requires.

Both are *mixin* base classes.  Domain args modules subclass one or
both and add their per-tool fields.  The discriminator policy is the
tool registration name (the existing handler-key lookup), so unlike
the WS event union we don't need a per-variant ``Literal`` tag.

Polymorphic ``dict[str, object]`` fields
----------------------------------------

A handful of domain args models declare polymorphic fields with a
``dict[str, object]`` type:

* ``updates`` (CRUD update endpoints) -- the partial-update bag whose
  keys depend on the entity type and which keys are settable per
  domain.  Service layers are the source of truth for the allowed
  shape.
* ``parameters`` (workflow / report / simulation start / generate
  endpoints) -- caller-supplied execution parameters whose schema is
  published per workflow / report / scenario, not per tool.
* ``config`` / ``credentials`` (OAuth / connection setup endpoints) --
  per-provider freeform config; the surface is a one-of of dozens of
  provider schemas with no closed union and is enforced by the
  service layer when the provider is selected.
* ``steps`` (workflow create endpoints) -- a tuple of per-step
  dictionaries whose shape is the workflow-step union owned by
  ``synthorg.workflows.models``.

These are intentionally typed as ``dict[str, object]`` (not
``dict[str, Any]``) so callers cannot smuggle non-JSON values
(functions, modules) past validation; the inner schema is enforced
at the service layer where the polymorphic dispatch happens.  When a
new ``dict[str, object]`` field is added it MUST live in this
allowlist; reach for a typed nested model first.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

from synthorg.core.types import NotBlankStr


def _validate_iso_8601_aware_datetime(value: str) -> str:
    """Reject strings that are not timezone-aware ISO 8601 datetimes.

    Mirrors :func:`synthorg.meta.mcp.handlers.common_args._parse_iso_datetime`
    so the typed-args boundary catches the same shape of bad input the
    legacy dict-path helper rejects.  Returns ``value`` unchanged on
    success; handlers continue to receive the raw string and pass it to
    :func:`parse_time_window` for the final tz-aware parse + ordering
    check.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        msg = "value is not a valid ISO 8601 datetime"
        raise ValueError(msg) from exc
    if parsed.tzinfo is None:
        msg = "value must include a timezone (e.g. ``+00:00`` or ``Z``)"
        raise ValueError(msg)
    return value


IsoDatetimeStr = Annotated[
    NotBlankStr, AfterValidator(_validate_iso_8601_aware_datetime)
]
"""Non-blank string that parses as a timezone-aware ISO 8601 datetime.

Use for ``since`` / ``until`` filter fields whose wire schema documents
``"Start datetime (ISO 8601)"``.  The args-model boundary validates the
format up front; handlers still receive a ``str`` so the existing
``parse_time_window`` call sites keep working unchanged.
"""

_ARGS_CONFIG = ConfigDict(
    frozen=True,
    allow_inf_nan=False,
    extra="forbid",
)


class _ArgsBase(BaseModel):
    """Common config for every MCP domain args model.

    Frozen + extra=forbid + no-NaN are the project-wide defaults; the
    individual fields each model adds describe its tool's specific
    payload shape.
    """

    model_config = _ARGS_CONFIG


class PaginationFields(_ArgsBase):
    """Pagination mixin for paginated list operations.

    Bounds and defaults MUST stay in lockstep with
    :func:`synthorg.meta.mcp.handlers.common_args.coerce_pagination`
    (the legacy validator used by handlers that still take
    ``arguments: dict``).  ``offset >= 0``, ``limit > 0`` and
    ``limit <= 500`` mirror its bounds; the ``default=50`` mirrors
    ``DEFAULT_LIMIT`` in that module.  When changing one, change the
    other in the same commit.
    """

    offset: int = Field(default=0, ge=0, description="Pagination offset")
    limit: int = Field(default=50, gt=0, le=500, description="Page size")


class AdminGuardrailFields(_ArgsBase):
    """Admin-op guardrails mixin.

    Promotes the runtime ``require_admin_guardrails`` check to
    validation: ``confirm`` must be the literal ``True`` (truthy
    non-bool values are rejected by the Literal[True] validator), and
    ``reason`` must be a non-blank string with at least one
    non-whitespace character.
    """

    confirm: Literal[True] = Field(
        description="Must be True to confirm the admin operation",
    )
    reason: NotBlankStr = Field(
        description="Operator-supplied reason for audit trail",
    )

    @field_validator("confirm", mode="before")
    @classmethod
    def _confirm_must_be_python_bool(cls, value: Any) -> Any:
        """Reject truthy non-bool ``confirm`` values.

        Pydantic's ``Literal[True]`` validator coerces any truthy
        value (``1``, ``"true"``, ``"yes"``) to ``True``; the
        ``require_admin_guardrails`` helper rejects those, and the
        mixin preserves that semantics.
        """
        if not isinstance(value, bool):
            # ruff noqa: TRY004 -- ValueError is what Pydantic converts
            #     into a clean ValidationError; TypeError bubbles past
            #     the validator wrapper, so we keep ValueError here.
            msg = "confirm must be the literal Python bool True"
            raise ValueError(msg)  # noqa: TRY004
        return value


__all__ = [
    "AdminGuardrailFields",
    "IsoDatetimeStr",
    "PaginationFields",
    "_ArgsBase",
]
