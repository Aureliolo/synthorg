"""Stateless URL- and header-guard helpers for the external API tool.

Pure functions with no governance state: the restricted-header set, the
path-traversal predicate, and the agent-header sanitisation that keeps the
approval signature and the egress request in lockstep.
"""

from urllib.parse import unquote, urlsplit

# Hop-by-hop / framing headers an agent must never set: ``Host`` would allow
# virtual-host injection past the egress host check, and the framing headers
# let an agent desync the request body from the transport.
_RESTRICTED_REQUEST_HEADERS: frozenset[str] = frozenset(
    {"host", "content-length", "transfer-encoding"},
)


def _has_dot_segment(url: str) -> bool:
    """Whether the URL path contains a ``.`` or ``..`` traversal segment.

    The path is percent-decoded first so encoded traversal sequences
    (``%2e`` / ``%2e%2e``) that an upstream server would normalise back
    into ``.`` / ``..`` are detected here rather than slipping past.
    Backslashes are folded to forward slashes first because IIS and some
    reverse proxies treat a backslash as a path separator, so a
    backslash-delimited ``..`` traversal would otherwise evade a
    forward-slash-only split.

    Returns:
        ``True`` when the predicate holds, ``False`` otherwise.
    """
    path = unquote(urlsplit(url).path).replace("\\", "/")
    return any(segment in {".", ".."} for segment in path.split("/"))


def _signable_headers(agent_headers: dict[str, str]) -> dict[str, str]:
    """Agent headers minus restricted ones that egress would strip.

    The approval signature must describe the request that is actually
    sent, so restricted headers (``Host`` / framing) -- which
    ``_merge_agent_headers`` drops before egress -- must not influence
    the signature either. Otherwise two calls differing only in a
    never-sent ``Host`` would sign differently and force a redundant
    re-approval.

    Returns:
        Mapping from ``str`` to ``str``.
    """
    return {
        k: v
        for k, v in agent_headers.items()
        if k.lower() not in _RESTRICTED_REQUEST_HEADERS
    }


def _merge_agent_headers(
    agent_headers: dict[str, str],
    brokered_headers: dict[str, str],
) -> dict[str, str]:
    """Layer agent headers under brokered ones, case-insensitively.

    Agent-supplied headers are dropped when they are restricted
    (``Host`` / framing headers) or collide case-insensitively with a
    brokered header, so an agent can neither inject a forged ``Host``
    nor shadow a brokered credential with a differently-cased
    duplicate. Brokered headers always win.

    Returns:
        Mapping from ``str`` to ``str``.
    """
    brokered_keys = {k.lower() for k in brokered_headers}
    safe_agent_headers = {
        k: v
        for k, v in _signable_headers(agent_headers).items()
        if k.lower() not in brokered_keys
    }
    return {**safe_agent_headers, **brokered_headers}
