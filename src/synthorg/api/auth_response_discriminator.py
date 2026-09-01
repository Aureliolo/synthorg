"""401 ``NotAuthorizedException`` detail-string discriminator.

Maps the auth middleware's five detail strings to specific RFC 9457
error codes so the dashboard can distinguish "fresh page load, no
token yet" (silent redirect) from "expired session" (toast +
redirect).

The five, and where each lands: "Missing authentication" and
"Invalid session cookie" carry the browser's two cold-start cases;
"Invalid JWT token" is the Authorization header's own failure;
"Invalid authorization scheme" and "Invalid credentials" collapse
into the generic code, deliberately and without the unknown-detail
warning, because they are recognised rather than unclassified.

The detail string also carries WHICH credential failed, and that
decides the audience: a cookie failure belongs to a browser with a
session to renew, while an Authorization-header failure belongs to a
CLI, API-key or service caller holding a minted token, for whom
session advice names a remedy they cannot perform.

Lives in its own module so the registry-style
:mod:`synthorg.api.exception_handlers` does not creep over the
800-line soft limit. Both producer (the auth middleware) and
consumer (this discriminator) are owned by SynthOrg, so the detail
strings are a stable internal contract; new strings drop through to
generic ``UNAUTHORIZED`` and emit an operator-visible warning so
divergence is caught quickly.
"""

from typing import Final

from synthorg.core.error_taxonomy import ErrorCode
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_AUTH_DISCRIMINATOR_UNKNOWN_DETAIL

logger = get_logger(__name__)

# Bound the logged detail length so a future producer that
# constructs the string from runtime context cannot bloat the log
# pipeline. Detail strings owned by SynthOrg middleware are short
# (under 64 chars today); 512 is a generous ceiling.
_MAX_DETAIL_LEN: Final[int] = 512


def discriminate_unauthorized(detail: str | None) -> tuple[ErrorCode, str]:
    """Map an auth middleware detail string to a discriminated error_code.

    Lets the dashboard treat "fresh session, no token" (a normal cold
    page load when the browser does not yet have a cookie) differently
    from "expired session" (the user had one but it lapsed). The UI
    shows the login form in both cases but only toasts on expiry,
    avoiding false-positive "you were signed out" messages on the
    very first load. A rejected bearer token takes a third code: no
    browser reaches it, so it is answered with the condition rather
    than with session advice.

    The mapping reads ``exc.detail`` literally. New detail strings
    fall through to the generic ``UNAUTHORIZED`` code AND emit a
    WARN-level log so operators catch the divergence between the
    producer (``synthorg.api.auth.middleware``) and this consumer.

    Args:
        detail: The ``NotAuthorizedException.detail`` string emitted by
            the auth middleware. ``None`` is treated as an unrecognised
            detail (falls through with a warning).

    Returns:
        ``(error_code, response_message)`` tuple suitable for the
        exception handler's RFC 9457 envelope builder.
    """
    match detail:
        case "Missing authentication":
            return (
                ErrorCode.SESSION_NO_TOKEN,
                "Authentication required",
            )
        case "Invalid session cookie":
            return (
                ErrorCode.SESSION_EXPIRED,
                "Session expired. Please log in again.",
            )
        case "Invalid JWT token":
            # Reached only from the Authorization header, which the browser
            # never uses (it authenticates by cookie, whose failure is the
            # branch above). The callers here hold a minted token and have no
            # session to renew, so "log in again" names a remedy none of them
            # can perform and points at the wrong cause: what actually failed
            # is the token's signature, required claims, or expiry.
            return (
                ErrorCode.BEARER_TOKEN_INVALID,
                (
                    "Bearer token rejected: its signature, required claims, "
                    "or expiry failed validation."
                ),
            )
        case "Invalid authorization scheme" | "Invalid credentials":
            # Known bad-credential / wrong-scheme failures (e.g. an API-key
            # caller). Map to the generic 401 WITHOUT the unknown-detail
            # warning below, which would otherwise fire on every such request.
            return (
                ErrorCode.UNAUTHORIZED,
                "Authentication required",
            )
        case _:
            # Detail string is owned by SynthOrg middleware (not user
            # input), so direct embedding is safe; truncate defensively
            # in case a future producer constructs it from runtime
            # context.
            logger.warning(
                API_AUTH_DISCRIMINATOR_UNKNOWN_DETAIL,
                detail=(detail or "<none>")[:_MAX_DETAIL_LEN],
            )
            return (ErrorCode.UNAUTHORIZED, "Authentication required")
