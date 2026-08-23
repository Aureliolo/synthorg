"""Rebuild a URL's authority around a validated host.

The git-clone curl-resolve pin (:func:`synthorg.tools._git_clone._with_validated_host`)
and the web-fetch DNS pin (:func:`synthorg.tools.web._guarded_fetch.pin_url`) both
need to replace a URL's authority with the hostname or IP an SSRF validator
resolved, while preserving whatever userinfo the caller's URL stated and
bracketing an IPv6 literal so its own colons stay apart from the port
separator. A second copy of that assembly is a copy that can drift from the
DNS-rebinding fix it exists to support.
"""

from urllib.parse import ParseResult, urlunparse


def bracket_host(host: str) -> str:
    """Bracket *host* for use in a URL authority when it is an IPv6 literal.

    Returns:
        ``host`` wrapped in ``[...]`` when it contains a colon, unchanged
        otherwise.
    """
    return f"[{host}]" if ":" in host else host


def with_authority_host(
    parsed: ParseResult,
    host: str,
    port: int | None,
) -> str:
    """Rebuild the URL *parsed* came from, replacing its host with *host*.

    *host* is bracketed automatically when it is an IPv6 literal. Any
    userinfo *parsed* carried is kept, since it is part of the request the
    caller authored.

    A ``SplitResult`` is refused at the type level rather than accepted and
    handled: it is one field shorter, so ``urlunparse`` reads its fragment as
    the params slot and raises. The sibling readers here take either, because
    reading ``.hostname`` and ``.port`` needs no such agreement; only the
    rebuild does.

    Args:
        parsed: The caller's URL, already parsed by ``urlparse``.
        host: The validated hostname or IP to carry in the rebuilt URL.
        port: The port to state explicitly, or ``None`` to state none.

    Returns:
        The URL with its authority replaced.
    """
    bracketed = bracket_host(host)
    authority = f"{bracketed}:{port}" if port is not None else bracketed
    userinfo = parsed.netloc.rpartition("@")[0]
    netloc = f"{userinfo}@{authority}" if userinfo else authority
    return urlunparse(parsed._replace(netloc=netloc))
