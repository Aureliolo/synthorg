"""Centralised argument-validation helpers for MCP tool handlers.

Single source of truth for every argument extraction / coercion
routine that handlers reach for: ``_require_non_blank``,
``_actor_id``, ``_get_str`` and their siblings live here so each
domain handler imports the same validators instead of carrying its
own near-identical copy.

The module groups its helpers in three buckets:

* **Typed required extraction**: :func:`require_arg`,
  :func:`require_non_blank`, :func:`require_dict`,
  :func:`coerce_pagination`.
* **Optional extraction**: :func:`get_optional_str`,
  :func:`parse_str_sequence`, :func:`parse_time_window`.
* **Actor identity**: :func:`actor_id` (optional),
  :func:`require_actor_id` (raising), :func:`actor_label` (non-blank
  audit string with configurable fallback).

Every "raise" path produces an ``ArgumentValidationError`` so the
handler can convert it to a stable ``err(...)`` envelope without
catching framework-specific exceptions. The expected-type description
in the error message is the only diagnostic detail that should differ
between helpers; tests assert on ``error_type=ArgumentValidationError``
rather than the description text.
"""

import copy
from datetime import UTC, datetime
from typing import Final, cast, overload

from synthorg.core.iso_datetime import parse_iso_utc
from synthorg.core.types import NotBlankStr
from synthorg.meta.mcp.errors import ArgumentValidationError

# Shared with ``common.paginate_sequence`` (the in-memory pager applies the
# same offset/limit bounds), so these are the public single source of truth.
PAGINATION_ARG_OFFSET = "offset"
PAGINATION_ARG_LIMIT = "limit"
PAGINATION_TY_NON_NEG_INT = "non-negative int"
PAGINATION_TY_POS_INT = "positive int"

_ARG_OFFSET = PAGINATION_ARG_OFFSET
_ARG_LIMIT = PAGINATION_ARG_LIMIT
_ARG_SINCE = "since"
_ARG_UNTIL = "until"
_ARG_ACTOR = "actor"

_TY_NON_NEG_INT = PAGINATION_TY_NON_NEG_INT
_TY_POS_INT = PAGINATION_TY_POS_INT
_TY_NON_BLANK = "non-blank string"
_TY_AGENT = "identified agent"
_TY_ISO_DT = "ISO 8601 datetime string"
_TY_TZ_AWARE = "timezone-aware ISO 8601"
_TY_WINDOW_ORDER = "before until"
_TY_STR_SEQ = "sequence of non-blank strings"
_TY_DICT_OBJ = "mapping of str -> object"
_TY_DICT_OF_STR = "mapping of str -> str"

_DEFAULT_ACTOR_FALLBACK: NotBlankStr = NotBlankStr("mcp-anonymous")
"""Module-level default for ``actor_label`` -- avoids B008 in the signature."""


def require_arg[T](arguments: dict[str, object], key: str, ty: type[T]) -> T:
    """Extract a typed required argument or raise ``ArgumentValidationError``.

    ``bool`` is explicitly rejected when ``ty is int`` so that a sloppy
    ``confirm=True`` never satisfies an int field. ``None`` is always
    treated as missing, regardless of the declared type.

    Args:
        arguments: Parsed tool arguments.
        key: Argument name.
        ty: Expected Python type (``str``, ``int``, ``bool``, etc.).

    Returns:
        The argument value, narrowed to ``ty``.

    Raises:
        ArgumentValidationError: If missing, ``None``, or wrongly typed.
    """
    if key not in arguments or arguments[key] is None:
        raise ArgumentValidationError(key, ty.__name__)
    value = arguments[key]
    if ty is int and isinstance(value, bool):
        raise ArgumentValidationError(key, "int")
    if not isinstance(value, ty):
        raise ArgumentValidationError(key, ty.__name__)
    return value


def require_non_blank(arguments: dict[str, object], key: str) -> str:
    """Extract a non-blank, stripped string argument or raise.

    Centralised so every handler does the same validation: missing,
    non-string, blank-after-strip all map to ``ArgumentValidationError``
    with ``domain_code=invalid_argument``.

    Args:
        arguments: Parsed tool arguments.
        key: Argument name.

    Returns:
        The argument value with surrounding whitespace stripped.

    Raises:
        ArgumentValidationError: If missing, not a string, or blank after
            stripping.
    """
    raw = arguments.get(key)
    if not isinstance(raw, str) or not raw.strip():
        raise ArgumentValidationError(key, _TY_NON_BLANK)
    return raw.strip()


def require_non_blank_value(value: object, arg_name: str) -> str:
    """Validate an already-extracted value is a non-blank stripped string.

    Companion to :func:`require_non_blank` for cases where the value
    has already been pulled out of the arguments dict (e.g. iterating
    list elements where the parent list has been validated but each
    element still needs the non-blank check). Uses the same
    ``ArgumentValidationError`` / ``domain_code=invalid_argument``
    contract so handler call sites get the same envelope shape.

    Args:
        value: Extracted candidate value (any type).
        arg_name: Argument or field name for the error envelope.

    Returns:
        The value with surrounding whitespace stripped.

    Raises:
        ArgumentValidationError: If ``value`` is not a string, or is
            blank after stripping.
    """
    if not isinstance(value, str) or not value.strip():
        raise ArgumentValidationError(arg_name, _TY_NON_BLANK)
    return value.strip()


def get_optional_str(
    arguments: dict[str, object],
    key: str,
) -> NotBlankStr | None:
    """Extract an optional non-blank string argument; ``None`` when absent.

    Distinguishes "missing / empty" (returns ``None``) from "wrong type
    or whitespace-only" (raises). Subsumes the per-domain ``_get_str``
    duplicates verbatim.

    Note: the returned value is **not** stripped. The non-blank check
    uses the stripped form, but the original (with leading/trailing
    whitespace, if any) is preserved in the result -- callers that
    need the canonical form should call ``.strip()`` themselves or
    use :func:`require_non_blank` for required arguments.

    Args:
        arguments: Parsed tool arguments.
        key: Argument name.

    Returns:
        The argument value as ``NotBlankStr``, or ``None`` when the key
        is missing / its value is ``None`` / its value is the empty
        string.

    Raises:
        ArgumentValidationError: If the value is present but not a
            non-blank string.
    """
    raw = arguments.get(key)
    if raw in (None, ""):
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ArgumentValidationError(key, _TY_NON_BLANK)
    return NotBlankStr(raw)


@overload
def require_dict(
    arguments: dict[str, object],
    key: str,
    *,
    value_type: type[str],
    deep_copy: bool = ...,
) -> dict[str, str]: ...


@overload
def require_dict(
    arguments: dict[str, object],
    key: str,
    *,
    value_type: None = ...,
    deep_copy: bool = ...,
) -> dict[str, object]: ...


def require_dict(
    arguments: dict[str, object],
    key: str,
    *,
    value_type: type | None = None,
    deep_copy: bool = True,
) -> dict[str, str] | dict[str, object]:
    """Require a dict argument; optionally enforce uniform value type + copy.

    Subsumes the two divergent ``_require_dict`` duplicates:

    * ``communication.py`` validated ``dict[str, str]``: pass
      ``value_type=str``.
    * ``organization.py`` deep-copied to decouple the handler's view
      from the caller-supplied payload: keep the default
      ``deep_copy=True``.

    The default behaviour (``value_type=None``, ``deep_copy=True``) is
    the safer of the two: a deep copy on the way in, no value-type
    constraint. Callers opt into stricter shapes explicitly.

    Args:
        arguments: Parsed tool arguments.
        key: Argument name.
        value_type: Optional uniform type every value in the dict must
            satisfy. ``None`` means values are unconstrained.
        deep_copy: When ``True`` (default), returns a fresh deep copy so
            handler mutations cannot ripple into the caller's payload.
            When ``False``, returns ``dict(raw)`` -- a new outer dict but
            shared nested mutables.

    Returns:
        The (optionally deep-copied) dict.

    Raises:
        ArgumentValidationError: If missing, not a dict, has a non-str
            key, or has a value that fails ``value_type``.
    """
    expected = _TY_DICT_OF_STR if value_type is str else _TY_DICT_OBJ
    raw = arguments.get(key)
    if not isinstance(raw, dict):
        raise ArgumentValidationError(key, expected)
    for k, v in raw.items():
        if not isinstance(k, str):
            raise ArgumentValidationError(key, expected)
        if value_type is not None and not isinstance(v, value_type):
            raise ArgumentValidationError(key, expected)
    if deep_copy:
        return copy.deepcopy(raw)
    return dict(raw)


def parse_str_sequence(
    arguments: dict[str, object],
    key: str,
) -> tuple[NotBlankStr, ...] | None:
    """Parse an optional sequence-of-non-blank-strings argument.

    Returns ``None`` when the argument is missing / ``None`` / the empty
    string. An empty list / tuple is a valid parse and returns an empty
    tuple; callers that need non-empty enforce that themselves.

    Args:
        arguments: Parsed tool arguments.
        key: Argument name.

    Returns:
        Tuple of non-blank strings, or ``None`` when absent.

    Raises:
        ArgumentValidationError: If the value is present but is not a
            list/tuple, or any entry is not a non-blank string.
    """
    raw = arguments.get(key)
    if raw in (None, ""):
        return None
    if not isinstance(raw, (list, tuple)):
        raise ArgumentValidationError(key, _TY_STR_SEQ)
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ArgumentValidationError(key, _TY_STR_SEQ)
    return tuple(NotBlankStr(item) for item in raw)


def _now_utc() -> datetime:
    """Return the current UTC time.

    Wrapper around ``datetime.now(UTC)`` so tests can patch this single
    seam (the stdlib ``datetime`` class is immutable and cannot be
    patched directly).

    Returns:
        ``datetime`` instance.
    """
    return datetime.now(UTC)


def _parse_iso_datetime(raw: object, arg_name: str) -> datetime:
    """Parse a timezone-aware ISO 8601 datetime arg or raise.

    Shared by :func:`parse_time_window` for both ``since`` and ``until``
    so the parsing-and-tzinfo-check pair lives in one place.

    Returns:
        ``datetime`` instance.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ArgumentValidationError(arg_name, _TY_ISO_DT)
    try:
        return parse_iso_utc(raw)
    except ValueError as exc:
        raise ArgumentValidationError(arg_name, _TY_TZ_AWARE) from exc


def parse_time_window(
    arguments: dict[str, object],
    *,
    until_required: bool = True,
) -> tuple[datetime, datetime]:
    """Parse the ``since`` / ``until`` ISO 8601 datetime arg pair.

    Both must be timezone-aware ISO 8601 strings; naive values are
    rejected. ``since`` must be strictly earlier than ``until``.

    Args:
        arguments: Parsed tool arguments.
        until_required: When ``True`` (default), a missing ``until``
            raises. When ``False``, missing ``until`` defaults to
            ``datetime.now(UTC)`` -- preserves the signals.py and
            ``analytics.until_required=False`` call-site behaviour.

    Returns:
        ``(since, until)`` -- both timezone-aware datetimes.

    Raises:
        ArgumentValidationError: On any of: missing/blank ``since``;
            unparseable ``since`` or ``until``; naive datetime; missing
            ``until`` while required; ``since >= until``.
    """
    return resolve_time_window(
        arguments.get(_ARG_SINCE),
        arguments.get(_ARG_UNTIL),
        until_required=until_required,
    )


def resolve_time_window(
    since_raw: object,
    until_raw: object,
    *,
    until_required: bool = True,
) -> tuple[datetime, datetime]:
    """Resolve an already-extracted ``since`` / ``until`` pair to datetimes.

    The typed-args boundary (``IsoDatetimeStr``) validates the ISO 8601 +
    timezone-aware shape up front; this applies the same ``until`` default
    and strict ordering the dict-path :func:`parse_time_window` enforces, so
    handlers that read the window through ``typed_args`` keep identical
    semantics without touching the raw ``arguments`` dict.

    Args:
        since_raw: The ``since`` value (typically a validated ISO string).
        until_raw: The ``until`` value, or ``None`` / ``""`` when absent.
        until_required: When ``True``, a missing ``until`` raises; when
            ``False`` it defaults to ``datetime.now(UTC)``.

    Returns:
        ``(since, until)`` -- both timezone-aware datetimes.

    Raises:
        ArgumentValidationError: On missing/unparseable/naive ``since``;
            unparseable ``until``; missing ``until`` while required; or
            ``since >= until``.
    """
    since = _parse_iso_datetime(since_raw, _ARG_SINCE)
    if until_raw in (None, ""):
        if until_required:
            raise ArgumentValidationError(_ARG_UNTIL, _TY_ISO_DT)
        until = _now_utc()
    else:
        until = _parse_iso_datetime(until_raw, _ARG_UNTIL)
    if since >= until:
        raise ArgumentValidationError(_ARG_SINCE, _TY_WINDOW_ORDER)
    return since, until


_DEFAULT_DEFAULT_LIMIT: Final[int] = 50


def coerce_pagination(
    arguments: dict[str, object],
    *,
    default_limit: int = _DEFAULT_DEFAULT_LIMIT,
) -> tuple[int, int]:
    """Parse ``offset``/``limit`` as ints with strict bounds + bool rejection.

    Shared pagination coercion for every handler that accepts a page
    slice. Each branch raises :class:`ArgumentValidationError`
    (``domain_code=invalid_argument``) so callers get a stable envelope
    instead of a raw ``TypeError``/``ValueError``:

    - missing / empty-string value -> default (``offset=0``,
      ``limit=default_limit``);
    - ``bool`` literal (``True``/``False``) is rejected even though
      ``int(True) == 1`` -- booleans are a type confusion, not a valid
      pagination value;
    - non-coercible value (non-numeric string, mapping, etc.) -> invalid;
    - coerced int fails the bound check (``offset >= 0`` /
      ``limit > 0``) -> invalid.

    Args:
        arguments: Parsed MCP tool arguments.
        default_limit: Page size when ``limit`` is missing or empty.

    Returns:
        Tuple of ``(offset, limit)`` both guaranteed to satisfy the
        bounds.

    Raises:
        ArgumentValidationError: On any of the failure modes above.
    """
    raw_offset: object = arguments.get(_ARG_OFFSET)
    raw_limit: object = arguments.get(_ARG_LIMIT)
    offset = _coerce_bounded_int(
        raw_offset,
        arg_name=_ARG_OFFSET,
        expected=_TY_NON_NEG_INT,
        default=0,
        lower=0,
    )
    limit = _coerce_bounded_int(
        raw_limit,
        arg_name=_ARG_LIMIT,
        expected=_TY_POS_INT,
        default=default_limit,
        lower=1,
    )
    return offset, limit


def _coerce_bounded_int(
    raw: object,
    *,
    arg_name: str,
    expected: str,
    default: int,
    lower: int,
) -> int:
    """Coerce ``raw`` to int >= ``lower`` or raise ``ArgumentValidationError``.

    The ``default`` is validated through the same gate so a caller
    passing ``default=0`` or ``default=True`` cannot smuggle an invalid
    value past the check when ``raw`` is missing.

    Returns:
        Resulting integer.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    if raw is None or raw == "":
        if isinstance(default, bool) or not isinstance(default, int):
            raise ArgumentValidationError(arg_name, expected)
        if default < lower:
            raise ArgumentValidationError(arg_name, expected)
        return default
    if isinstance(raw, bool):
        raise ArgumentValidationError(arg_name, expected)
    try:
        value = int(cast("str | int | float", raw))
    except (TypeError, ValueError) as exc:
        raise ArgumentValidationError(arg_name, expected) from exc
    if value < lower:
        raise ArgumentValidationError(arg_name, expected)
    return value


def actor_id(actor: object) -> str | None:
    """Return a stable audit identifier for ``actor`` (prefers ``.id``).

    Returns ``None`` when ``actor`` is ``None`` or carries neither a
    non-``None`` ``.id`` nor a non-blank ``.name``. The ``.name``
    fallback is stripped before returning so a whitespace-only name
    (which would otherwise satisfy raw truthiness) is rejected -- the
    audit attribution surface is not allowed to record blank
    identifiers. Centralised so every handler emits the same identifier
    shape into audit events and service calls.

    Returns:
        The ``str`` value when present, ``None`` otherwise.
    """
    if actor is None:
        return None
    agent_id = getattr(actor, "id", None)
    if agent_id is not None:
        return str(agent_id)
    name = getattr(actor, "name", None)
    if isinstance(name, str):
        stripped = name.strip()
        return stripped or None
    return None


def require_actor_id(actor: object) -> str:
    """Raising counterpart of :func:`actor_id`.

    Used by handlers that record actor attribution into a service call
    as a required field (e.g. approval reviewer). The unattributable
    case (``None`` actor, or actor with no ``.id`` and a blank ``.name``)
    is a wire-format violation, not a runtime fault, so it raises
    ``ArgumentValidationError`` and the handler returns a stable
    ``err(...)`` envelope.

    Args:
        actor: The calling agent identity, or ``None``.

    Returns:
        The actor's stable audit identifier.

    Raises:
        ArgumentValidationError: If ``actor`` carries no usable
            identifier.
    """
    identifier = actor_id(actor)
    if identifier is None:
        raise ArgumentValidationError(_ARG_ACTOR, _TY_AGENT)
    return identifier


def actor_label(
    actor: object,
    *,
    fallback: NotBlankStr = _DEFAULT_ACTOR_FALLBACK,
) -> NotBlankStr:
    """Return a guaranteed-non-blank attribution string for emit-only paths.

    Use this helper **only** when an attribution string is needed for
    logging, audit emit, or non-destructive service writes where a
    fallback identifier ("mcp-anonymous") is acceptable in place of a
    real actor. For paths that require a real actor (admin ops are
    already covered by ``require_admin_guardrails`` from ``common``,
    but non-admin writes that must not run anonymously) prefer
    :func:`require_actor_id`, which raises
    ``ArgumentValidationError`` when the actor cannot be identified.

    Subsumes the six per-domain ``_actor_name`` duplicates, including
    organization.py's stricter handling of blank string ``.id`` values
    (a whitespace-only string ``.id`` falls through to ``.name``).

    Resolution order:

    1. ``actor.id`` if present and (when string) non-blank after
       stripping; non-string ids (UUID, int) coerce via ``str()``;
    2. ``actor.name`` if a non-blank string;
    3. ``fallback``.

    Args:
        actor: Calling agent identity, or ``None``.
        fallback: Returned when ``actor`` is unattributable. Default
            ``"mcp-anonymous"`` matches the historical wire value.

    Returns:
        A guaranteed-non-blank attribution string.
    """
    if actor is None:
        return fallback
    raw_id = getattr(actor, "id", None)
    if raw_id is not None:
        if isinstance(raw_id, str):
            if raw_id.strip():
                return NotBlankStr(raw_id)
        else:
            return NotBlankStr(str(raw_id))
    name = getattr(actor, "name", None)
    if isinstance(name, str) and name.strip():
        return NotBlankStr(name)
    return fallback
