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

from typing import TYPE_CHECKING, Final

from synthorg.observability import get_logger

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = get_logger(__name__)

_BEARER_PARTS: Final[int] = 2


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


def compare_ci(a: str, b: str) -> bool:
    """Return ``True`` if ``a`` and ``b`` are equal under :func:`normalize_identifier`.

    Both inputs are stripped and case-folded before comparison, so
    ``compare_ci("  Alice ", "alice") is True`` and
    ``compare_ci("Straße", "STRASSE") is True``.

    Use this for single-string equality checks (HTTP scheme, role
    names, yes/no flags). For picking the first item out of an
    iterable of objects by attribute, prefer :func:`find_by_name_ci`.

    The helper is ``str``-only by design. Four ASGI-layer call
    sites that compare bytes-typed header names
    (``synthorg.api.auth.csrf`` and ``synthorg.api.etag``) keep
    the inline ``name.lower() == b"..."`` pattern: byte equality is
    idiomatic at that level and decoding to ``str`` purely to call
    this helper would add overhead in the hot request path.

    Args:
        a: First string.
        b: Second string.

    Returns:
        ``True`` if ``normalize_identifier(a) == normalize_identifier(b)``.
    """
    return normalize_identifier(a) == normalize_identifier(b)


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


def strip_trailing_slash(url: str) -> str:
    """Return ``url`` without trailing slashes; idempotent.

    Strips every trailing forward slash, mirroring the inline
    ``url.rstrip("/")`` pattern used at A2A agent-card, OAuth, OTLP,
    and provider-probing call sites. Empty input returns empty string.

    Args:
        url: URL or base URL string to strip.

    Returns:
        ``url`` with all trailing ``/`` characters removed.
    """
    return url.rstrip("/")


def normalize_optional_string(raw: str | None) -> str | None:
    """Strip whitespace; collapse empty-after-strip to ``None``.

    Replaces the inline ``(raw.strip() or None) if raw else None``
    pattern used in setup agents, workflow validation, and memory
    metadata fields where a blank user input should not be treated
    as a real value.

    Args:
        raw: Optional string from external input.

    Returns:
        ``None`` if ``raw`` is None or strips to empty, otherwise the
        stripped value.
    """
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def normalize_path(path: str | None) -> str:
    """Return a normalised URL path: strip trailing slashes, default to ``"/"``.

    Replaces the inline ``(path or "").rstrip("/") or "/"`` pattern
    used by CSRF validation, docs routing, and ETag-key matching
    where a missing or root-equivalent path must canonicalise to
    ``"/"`` for stable comparison.

    Args:
        path: Optional URL path (e.g. ``"/foo/"``, ``""``, ``None``).

    Returns:
        Path with trailing slashes stripped, or ``"/"`` if the result
        would otherwise be empty.
    """
    return (path or "").rstrip("/") or "/"


def normalize_ascii_lowercase(value: str) -> str:
    """Strip whitespace and lowercase using ASCII semantics.

    Use this for protocol identifiers where the spec prescribes ASCII
    case-insensitive equality: MIME media types (RFC 6838), boolean
    string parses (``"true"``/``"false"``), Docker memory-format
    suffixes, and ASCII-only dict-lookup keys.

    Differs from :func:`normalize_identifier`, which applies
    :py:meth:`str.casefold` for Unicode safety (German sharp-s,
    Greek final-sigma). For any user-supplied identifier that may
    contain non-ASCII characters, prefer :func:`normalize_identifier`.

    Args:
        value: The string to normalise.

    Returns:
        ``value`` with surrounding whitespace stripped and remaining
        ASCII characters lowercased.
    """
    stripped = value.strip()
    return stripped.lower()


def normalize_ascii_lowercase_or_default(
    value: str | None,
    *,
    default: str = "",
) -> str:
    """Normalise ``value`` (or ``default`` if blank) via ASCII lowercase.

    Replaces the ``(value or default)``-then-strip-then-lower idiom at
    NULL-coalescing call sites (model-affinity lookup, TLS-flag parse,
    setup boolean parse, classification-detector unit extraction).
    Whitespace-only inputs fall through to ``default`` so an operator
    submitting ``"   "`` for a TLS toggle gets the safe fallback rather
    than a silently empty result.

    Args:
        value: Optional string. ``None``, ``""``, and whitespace-only
            inputs all trigger the fallback.
        default: Fallback used when ``value`` is blank or None. Must
            already be in the desired final form; it is *not*
            re-normalised here (but is run through
            :func:`normalize_ascii_lowercase` along with the chosen
            input).

    Returns:
        ``normalize_ascii_lowercase(value)`` when ``value`` has
        non-whitespace content, else ``normalize_ascii_lowercase(default)``.
    """
    chosen = value if value and value.strip() else default
    return normalize_ascii_lowercase(chosen)


def extract_media_type(content_type_header: str) -> str:
    """Extract the MIME media type from a ``Content-Type`` header.

    ``"application/json; charset=utf-8"`` becomes ``"application/json"``.
    Strips parameters after the first ``;``, then ASCII-lowercases.
    Spec: RFC 6838 (media types are defined in the ASCII subset).

    Args:
        content_type_header: Raw header value, possibly with parameters.

    Returns:
        The lowercased media type alone, with surrounding whitespace
        removed. Empty input yields an empty string.
    """
    return normalize_ascii_lowercase(content_type_header.split(";", 1)[0])


def extract_bearer_token(header: str) -> str | None:
    """Extract the token from a ``Bearer <token>`` Authorization header.

    Case-insensitive on the scheme: ``"Bearer"``, ``"bearer"``,
    ``"BEARER"`` all parse. Returns ``None`` if the header is
    malformed (wrong number of fields), uses a non-bearer scheme,
    or carries a blank token.

    Args:
        header: Raw ``Authorization`` header value.

    Returns:
        The token portion, or ``None`` when the header does not
        match the ``Bearer <token>`` shape.
    """
    parts = header.split(None, 1)
    if len(parts) != _BEARER_PARTS or not compare_ci(parts[0], "bearer"):
        return None
    token = parts[1].strip()
    return token or None


def collapse_whitespace_lowercase(value: str) -> str:
    """Strip, ASCII-lowercase, and collapse internal whitespace runs to single spaces.

    Replaces the strip + lower + split + join idiom at command-pattern
    detection and prompt-normalisation sites where two inputs that
    differ only in whitespace runs should compare equal.

    Args:
        value: The string to canonicalise.

    Returns:
        ``value`` with surrounding whitespace stripped, ASCII-lowercased,
        and internal whitespace runs collapsed to single space characters.
    """
    return " ".join(normalize_ascii_lowercase(value).split())
