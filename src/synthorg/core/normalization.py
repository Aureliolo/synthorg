"""Unicode-safe normalization helpers.

Uses :py:meth:`str.casefold` (not :py:meth:`str.lower`) so
case-insensitive comparisons fold German sharp-s (``ß`` → ``ss``)
and Greek final-sigma forms consistently. Case-folding is
locale-independent: Turkish dotted-I (``İ``) folds to
``i`` + combining dot above, not Turkish-locale ``i``.

These helpers do **not** apply Unicode normalization (NFC/NFD).
Callers that need form equivalence (e.g. ``café`` written as
``e + combining acute`` vs precomposed ``é``) must normalize
upstream.
"""

from typing import TYPE_CHECKING

from synthorg.observability import get_logger

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = get_logger(__name__)


def normalize_identifier(value: str) -> str:
    """Normalize an identifier for case-insensitive comparison.

    Strips whitespace and applies locale-independent Unicode
    case-folding. Suitable for matching across Latin, German
    sharp-s, Greek, and Cyrillic scripts. Turkish dotted-I folds
    to the Unicode default form (``i`` + combining dot above), not
    the Turkish-locale plain ``i``; callers that need
    Turkish-locale semantics must handle that themselves.

    Does not apply Unicode NFC/NFD normalization; callers needing
    form equivalence must normalize upstream.

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
    """Return the first item whose ``name_attr`` matches ``target``.

    Works on any iterable of objects that expose a string attribute
    named ``name_attr`` (default ``"name"``). Both the target and
    each candidate value are run through :func:`normalize_identifier`
    before comparison, so the match is case- and
    whitespace-insensitive in both directions.

    Args:
        items: Iterable to scan linearly.
        target: Value to match (case- and whitespace-insensitive).
        name_attr: Attribute name holding the comparable string.

    Returns:
        The first item whose ``name_attr`` normalizes to the same
        value as ``target``, or ``None`` if none matches.
    """
    target_normalised = normalize_identifier(target)
    for item in items:
        value = getattr(item, name_attr, None)
        if isinstance(value, str) and normalize_identifier(value) == target_normalised:
            return item
    return None
