# module-kind: code
"""Canonical URL credential redaction for safe logging.

Single source of truth for the "parse a URL, remove or mask its
userinfo, and decide what to do with the query string" idiom that the
providers, communication-bus, and git-subprocess layers each grew their
own near-identical copy of. Every variant is reachable through one
:func:`redact_url` call by selecting the userinfo and query policies:

* providers self-URL logging strips userinfo and replaces the query with
  a ``<redacted>`` sentinel;
* the NATS bus masks userinfo as ``***@`` and drops the query entirely;
* the git-subprocess arg redactor strips userinfo but keeps the query.

The function never raises on a malformed URL or port -- redaction sits on
the logging path, so a parse failure degrades to returning the input
unchanged rather than aborting the log call.
"""

from typing import Final, Literal
from urllib.parse import urlsplit, urlunsplit

REDACTED_QUERY: Final[str] = "<redacted>"
"""Sentinel substituted for a non-empty query under the ``redact`` policy."""

USERINFO_MASK: Final[str] = "***"
"""Token substituted for userinfo when ``mask_userinfo`` is requested."""

QueryPolicy = Literal["strip", "redact", "keep"]


def redact_url(
    url: str,
    *,
    mask_userinfo: bool = False,
    query: QueryPolicy = "redact",
) -> str:
    """Return ``url`` with credentials removed for safe logging.

    Handles IPv6 literal hosts (brackets restored) and malformed ports
    (treated as absent) so this never raises during logging. A URL with
    no parseable hostname is returned unchanged.

    Args:
        url: URL to redact.
        mask_userinfo: When ``True``, a URL carrying userinfo keeps a
            ``***@`` marker so the log shows that credentials were
            present; when ``False`` (default) the userinfo is dropped
            entirely.
        query: Policy for the query string -- ``"redact"`` (default)
            replaces a non-empty query with :data:`REDACTED_QUERY`,
            ``"strip"`` removes it, ``"keep"`` preserves it verbatim.

    Returns:
        The redacted URL, or the original string when it has no host.
    """
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
    except ValueError:
        return url
    if hostname is None:
        return url
    # Restore IPv6 brackets so the rebuilt authority is unambiguous.
    if ":" in hostname:
        hostname = f"[{hostname}]"
    try:
        port = parts.port
    except ValueError:
        port = None
    authority = f"{hostname}:{port}" if port is not None else hostname
    has_creds = parts.username is not None or parts.password is not None
    if has_creds and mask_userinfo:
        authority = f"{USERINFO_MASK}@{authority}"
    if query == "keep":
        new_query = parts.query
    elif query == "redact":
        new_query = REDACTED_QUERY if parts.query else ""
    else:
        new_query = ""
    return urlunsplit((parts.scheme, authority, parts.path, new_query, parts.fragment))
