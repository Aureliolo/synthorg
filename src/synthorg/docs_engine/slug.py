"""Slug derivation for living documents.

Agents never supply slugs directly via the write tool: the service
derives ``slug = kebab(title)`` and resolves collisions with a numeric
suffix (``-2``, ``-3``, ...) against existing slugs in the same
project + doc_type bucket. Slugs are bounded to keep filesystem and
URL paths short.
"""

from collections.abc import Container

from synthorg.core.slug import kebab_slug
from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.constants import DOCS_SLUG_MAX_LENGTH
from synthorg.docs_engine.errors import DocValidationError
from synthorg.observability import get_logger
from synthorg.observability.events.docs import DOC_VALIDATION_FAILED

logger = get_logger(__name__)

_FALLBACK_SLUG: NotBlankStr = NotBlankStr("doc")
_MAX_SUFFIX: int = 10_000


def derive_slug(
    title: str,
    *,
    existing_slugs: Container[str],
) -> NotBlankStr:
    """Return a unique slug derived from *title*.

    The base form is the kebab-cased ASCII reduction of the title,
    bounded by :data:`DOCS_SLUG_MAX_LENGTH`. On collision the function
    appends ``-2``, ``-3``, ... until the resulting slug is not in
    *existing_slugs*. Pure function; deterministic for a given input.

    Args:
        title: Human-readable doc title.
        existing_slugs: Container exposing ``__contains__`` over the
            slugs already taken in the relevant scope.

    Returns:
        A ``NotBlankStr`` slug not present in *existing_slugs*.

    Raises:
        DocValidationError: When the suffix space is exhausted without
            finding a free slug.
    """
    base = _slugify(title)
    if base not in existing_slugs:
        return base
    suffix = 2
    while suffix < _MAX_SUFFIX:
        candidate = NotBlankStr(_truncate_with_suffix(base, suffix))
        if candidate not in existing_slugs:
            return candidate
        suffix += 1
    msg = f"Could not derive a unique slug from {title!r} (exhausted suffix space)"
    logger.warning(
        DOC_VALIDATION_FAILED,
        reason="slug_suffix_space_exhausted",
        error_type=DocValidationError.__name__,
    )
    raise DocValidationError(msg)


def _slugify(title: str) -> NotBlankStr:
    """Kebab-case ASCII reduction bounded by ``DOCS_SLUG_MAX_LENGTH``.

    Returns:
        The kebab-cased slug, or ``_FALLBACK_SLUG`` when the reduction is
        empty.
    """
    return NotBlankStr(
        kebab_slug(title, max_length=DOCS_SLUG_MAX_LENGTH, fallback=_FALLBACK_SLUG)
    )


def _truncate_with_suffix(base: str, suffix: int) -> str:
    """Append ``-N`` to *base*, preserving the overall length budget.

    Returns:
        ``base`` truncated to fit the suffix within
        ``DOCS_SLUG_MAX_LENGTH``, with ``-N`` appended.
    """
    tail = f"-{suffix}"
    budget = DOCS_SLUG_MAX_LENGTH - len(tail)
    return (base[:budget].rstrip("-") or "doc") + tail
