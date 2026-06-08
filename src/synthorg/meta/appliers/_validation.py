"""Shared validation helpers for meta-loop appliers.

These helpers are used by the ``dry_run()`` methods of the config,
prompt, and architecture appliers to validate proposals without
mutating system state.  They are intentionally pure and side-effect
free so they can be shared by the real ``apply()`` path when it
lands.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import ValidationError

__all__ = [
    "apply_diff_to_dict",
    "format_validation_errors",
    "parse_dotted_path",
    "validate_payload_keys",
]


class DottedPathError(
    ValueError,
):  # lint-allow: domain-error-hierarchy -- internal path-parse precondition
    """Raised when a dotted config path cannot be parsed or resolved."""


def parse_dotted_path(path: str) -> tuple[str, ...]:
    """Split a dotted path into a tuple of keys.

    Args:
        path: JSON-path style string, e.g. ``"budget.total_monthly"``.

    Returns:
        Tuple of non-blank path components.

    Raises:
        DottedPathError: If the path is empty, contains whitespace-only
            segments, or has leading/trailing / consecutive dots.
    """
    if not path or not path.strip():
        msg = "path must not be blank"
        raise DottedPathError(msg)
    stripped = path.strip()
    if stripped.startswith(".") or stripped.endswith("."):
        msg = f"path {path!r} must not start or end with '.'"
        raise DottedPathError(msg)
    parts = stripped.split(".")
    cleaned: list[str] = []
    for segment in parts:
        if not segment or not segment.strip() or segment != segment.strip():
            msg = f"path {path!r} contains a blank or whitespace segment"
            raise DottedPathError(msg)
        cleaned.append(segment)
    return tuple(cleaned)


def apply_diff_to_dict(
    data: dict[str, object],
    *,
    path: tuple[str, ...],
    new_value: object,
) -> None:
    """Apply ``new_value`` to ``data`` at the dotted ``path``.

    Walks the nested dict in place.  Intermediate keys must already
    exist (and map to dicts) -- an unknown segment is a bug in the
    proposal and surfaces as ``DottedPathError``.

    Args:
        data: Mutable dict (typically ``RootConfig.model_dump()``).
        path: Pre-parsed tuple of keys from ``parse_dotted_path``.
        new_value: Leaf value to set.

    Raises:
        DottedPathError: If an intermediate segment is unknown or not
            a dict (e.g. attempting to descend into a list).
    """
    if not path:
        msg = "path must not be empty"
        raise DottedPathError(msg)
    cursor: object = data
    for depth, key in enumerate(path[:-1]):
        if not isinstance(cursor, dict):
            msg = (
                f"cannot descend into non-dict at segment "
                f"{'.'.join(path[: depth + 1])!r}"
            )
            raise DottedPathError(msg)
        if key not in cursor:
            msg = f"unknown config path segment {'.'.join(path[: depth + 1])!r}"
            raise DottedPathError(msg)
        cursor = cursor[key]
    if not isinstance(cursor, dict):
        msg = f"cannot assign at non-dict parent {'.'.join(path[:-1])!r}"
        raise DottedPathError(msg)
    # Unknown leaf key is still a problem -- the corresponding field
    # would not exist on the Pydantic model and silently adding it
    # would let invalid paths slip through dry_run.
    if path[-1] not in cursor:
        msg = f"unknown config path {'.'.join(path)!r}"
        raise DottedPathError(msg)
    cursor[path[-1]] = new_value


def format_validation_errors(
    err: ValidationError,
    *,
    path_prefix: str | None = None,
) -> list[str]:
    """Render a Pydantic ``ValidationError`` into concise strings.

    Args:
        err: Validation error from ``model_validate``.
        path_prefix: Optional originating config path to prepend.

    Returns:
        One short message per sub-error, in deterministic order.
    """
    messages: list[str] = []
    for detail in err.errors():
        loc = ".".join(str(seg) for seg in detail.get("loc", ()))
        msg = detail.get("msg", "validation error")
        if path_prefix is not None:
            prefix_msg = (
                f"{path_prefix} -> {loc}: {msg}" if loc else f"{path_prefix}: {msg}"
            )
            messages.append(prefix_msg)
        else:
            messages.append(f"{loc}: {msg}" if loc else msg)
    return messages


def validate_payload_keys(
    payload: Mapping[str, object],
    *,
    required: frozenset[str],
    allowed: frozenset[str],
) -> list[str]:
    """Check that ``payload`` has the required keys and no unknown keys.

    Args:
        payload: Operation-specific payload dict.
        required: Keys that must be present (non-None).
        allowed: All permissible keys (including required).

    Returns:
        List of human-readable error messages.  Empty list means valid.
    """
    errors: list[str] = []
    missing = sorted(k for k in required if k not in payload or payload[k] is None)
    if missing:
        errors.append(f"missing required payload keys: {missing}")
    unknown = sorted(set(payload) - allowed)
    if unknown:
        errors.append(f"unknown payload keys: {unknown}")
    return errors
