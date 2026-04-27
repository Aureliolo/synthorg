"""Unicode-safe normalization helpers.

Uses :py:meth:`str.casefold` (not :py:meth:`str.lower`) so
case-insensitive comparisons behave correctly across Latin, German
sharp-s, Greek, and Turkish dotted-I pairs.
"""

from typing import TYPE_CHECKING

from synthorg.observability import get_logger

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = get_logger(__name__)


def normalize_identifier(value: str) -> str:
    """Normalize an identifier for case-insensitive comparison.

    Strips whitespace and applies Unicode case-folding for robust
    matching across Latin, Greek, Turkish, and other scripts.

    Args:
        value: Identifier to normalize (e.g. agent name, role, capability).

    Returns:
        Normalized string suitable for case-insensitive comparison.
    """
    return value.strip().casefold()


def find_by_name_ci[T](
    items: Iterable[T],
    target: str,
    *,
    name_attr: str = "name",
) -> T | None:
    """Return the first item whose ``name_attr`` casefolds to ``target``.

    Works on any iterable of objects that expose a string attribute
    named ``name_attr`` (default ``"name"``). Returns ``None`` when
    no match is found.

    Args:
        items: Iterable to scan linearly.
        target: Value to match (case- and whitespace-insensitive).
        name_attr: Attribute name holding the comparable string.

    Returns:
        The first matching item, or ``None``.
    """
    target_normalised = normalize_identifier(target)
    for item in items:
        value = getattr(item, name_attr, None)
        if isinstance(value, str) and normalize_identifier(value) == target_normalised:
            return item
    return None
