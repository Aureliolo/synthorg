"""401 ``NotAuthorizedException`` detail-string discriminator.

Maps the auth middleware's detail strings ("Missing authentication",
"Invalid session cookie", "Invalid JWT token") to specific RFC 9457
error codes so the dashboard can distinguish "fresh page load, no
token yet" (silent redirect) from "expired session" (toast +
redirect).

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
    from "expired token" (the user had a session but it lapsed). The
    UI shows the login form in both cases but only toasts on expiry,
    avoiding false-positive "you were signed out" messages on the
    very first load.

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
        case "Invalid session cookie" | "Invalid JWT token":
            return (
                ErrorCode.SESSION_EXPIRED,
                "Session expired. Please log in again.",
            )
        case _:
            # Detail string is owned by SynthOrg middleware (not user
            # input), so direct embedding is safe; truncate defensively
            # in case a future producer constructs it from runtime
            # context.
            logger.warning(
                "auth.discriminator.unknown_detail",
                detail=(detail or "<none>")[:_MAX_DETAIL_LEN],
            )
            return (ErrorCode.UNAUTHORIZED, "Authentication required")
