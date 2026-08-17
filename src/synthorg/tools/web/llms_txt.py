# module-kind: code
"""Point a fetch at a site's ``llms.txt`` index when it publishes one.

A growing share of documentation sites publish ``/llms.txt`` (a curated index
of their pages) and ``/llms-full.txt`` (those pages inlined). Both exist for
exactly the caller this module serves: an agent that would otherwise fetch N
pages to answer one question about a library.

This only ever reports a discovery. It never redirects the fetch the caller
asked for, because silently answering from a different URL than the one
requested makes the transcript a record of something that did not happen.
"""

from typing import Final
from urllib.parse import urlsplit, urlunsplit

import httpx

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.web import (
    WEB_LLMS_TXT_ABSENT,
    WEB_LLMS_TXT_DISCOVERED,
)
from synthorg.providers.url_utils import redact_url
from synthorg.tools.network_validator import (
    NetworkPolicy,
    is_allowed_http_scheme,
    validate_url_host,
)
from synthorg.tools.web._guarded_fetch import pin_url, stream_bounded

logger = get_logger(__name__)

LLMS_TXT_PATH: Final[str] = "/llms.txt"
LLMS_FULL_TXT_PATH: Final[str] = "/llms-full.txt"
_PROBE_MAX_BYTES: Final[int] = 2048
_HTTP_BAD_REQUEST: Final[int] = 400


def index_urls_for(url: str) -> tuple[str, str]:
    """Return the ``llms.txt`` and ``llms-full.txt`` URLs for *url*'s origin.

    Returns:
        The index URL and the full-content URL, both on the same origin.

    Raises:
        ValueError: If *url* carries no scheme or host to build an origin from.
    """
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        msg = f"cannot derive an origin from {url!r}"
        raise ValueError(msg)
    return (
        urlunsplit((parts.scheme, parts.netloc, LLMS_TXT_PATH, "", "")),
        urlunsplit((parts.scheme, parts.netloc, LLMS_FULL_TXT_PATH, "", "")),
    )


async def discover_llms_txt(
    url: str,
    *,
    network_policy: NetworkPolicy,
    timeout_seconds: float,
) -> str:
    """Probe *url*'s origin for an ``llms.txt`` index.

    Best-effort by construction: this runs alongside a fetch that already
    succeeded, so a probe that fails must never turn that into a failure. It
    reads a couple of kilobytes to confirm the file is really there and is not
    a soft-404 HTML page.

    Args:
        url: The page that was fetched, whose origin is probed.
        network_policy: SSRF policy applied to the probe.
        timeout_seconds: Per-probe timeout.

    Returns:
        The discovered index URL, or an empty string when the site publishes
        none or the probe could not establish that it does.
    """
    try:
        index_url, _ = index_urls_for(url)
    except ValueError:
        return ""
    if not is_allowed_http_scheme(index_url):
        return ""
    try:
        validation = await validate_url_host(index_url, network_policy)
        if isinstance(validation, str):
            return ""
        request_url, headers = pin_url(index_url, {"Accept": "text/plain"}, validation)
        raw, status, response_headers = await stream_bounded(
            request_url,
            "GET",
            headers=headers,
            body=None,
            timeout=timeout_seconds,
            max_bytes=_PROBE_MAX_BYTES,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- a probe for an optional file, running after
        # the caller's fetch already succeeded. Failing the fetch because an
        # extra convenience lookup failed would be strictly worse.
        reraise_critical(exc)
        logger.debug(
            WEB_LLMS_TXT_ABSENT,
            url=redact_url(index_url),
            reason="probe_error",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return ""

    if status >= _HTTP_BAD_REQUEST or not _looks_like_index(raw, response_headers):
        # DEBUG, not INFO: most sites publish no index, so this is the ordinary
        # outcome. It is logged at all because "the host has no index" and "the
        # probe never ran" are otherwise indistinguishable when someone asks
        # why a docs site never offered one.
        logger.debug(
            WEB_LLMS_TXT_ABSENT,
            url=redact_url(index_url),
            reason="not_an_index",
            status_code=status,
        )
        return ""
    logger.info(WEB_LLMS_TXT_DISCOVERED, url=redact_url(index_url), found=True)
    return index_url


def _looks_like_index(raw: bytes, headers: httpx.Headers) -> bool:
    """Whether the probe body is a real index rather than a soft 404.

    Many sites answer an unknown path with a 200 and their HTML shell, so a
    status check alone would report an index on every site that does.

    Returns:
        ``True`` when the response is plausibly an ``llms.txt``.
    """
    if not raw.strip():
        return False
    content_type = headers.get("Content-Type", "").lower()
    if "html" in content_type:
        return False
    head = raw[:_PROBE_MAX_BYTES].lstrip().lower()
    return not head.startswith((b"<!doctype", b"<html"))


def discovery_notice(index_url: str) -> str:
    """Render the one-line hint appended to a fetch result.

    Returns:
        The notice, or an empty string when nothing was discovered.
    """
    if not index_url:
        return ""
    return (
        f"\n\n[This site publishes an LLM documentation index at {index_url}. "
        "Fetching it lists the pages worth reading, which is usually cheaper "
        "than fetching pages one at a time.]"
    )


__all__ = [
    "LLMS_FULL_TXT_PATH",
    "LLMS_TXT_PATH",
    "discover_llms_txt",
    "discovery_notice",
    "index_urls_for",
]
