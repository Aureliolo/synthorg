"""Shared controller helper: name the operator behind an audited write.

Lives in its own module so controllers that record an actor on an audited write
(tasks, projects, ...) share one implementation instead of reaching across a
sibling controller's private boundary.
"""

from synthorg.api.auth.context import get_authenticated_user_id


def extract_requester() -> str:
    """Return the id of the user driving this request.

    Reads the request-scoped binding :class:`AuthContextMiddleware` installs,
    which is where the authenticated user actually is. Looking anywhere else
    misses on every request, including fully authenticated ones, and an
    audited write that misses records the transport in place of the operator
    while the warning meant to make an auth misconfiguration visible fires on
    every call instead of on a real one.

    Returns:
        The authenticated user id.

    Raises:
        AuthContextMissingError: No user is bound, which means the write is
            happening outside an authenticated request. Raised rather than
            substituted: an audited write that cannot name its actor is not
            one this helper may quietly complete.
    """
    return get_authenticated_user_id()
