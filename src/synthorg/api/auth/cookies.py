"""Cookie creation helpers for session, CSRF, and refresh tokens.

Stateless pure functions that produce Litestar ``Cookie`` objects.
Controllers attach these to ``Response`` objects.
"""

import secrets

from litestar.datastructures import Cookie

from synthorg.api.auth.config import AuthConfig  # noqa: TC001
from synthorg.api.auth.token_size import get_auth_token_bytes


def make_session_cookie(
    token: str,
    max_age: int,
    config: AuthConfig,
) -> Cookie:
    """Create an HttpOnly session cookie carrying the JWT.

    Args:
        token: Encoded JWT string.
        max_age: Cookie lifetime in seconds.
        config: Auth configuration.

    Returns:
        HttpOnly session cookie configured per AuthConfig.
    """
    return Cookie(
        key=config.cookie_name,
        value=token,
        httponly=True,
        secure=config.cookie_secure,
        samesite=config.cookie_samesite,
        path=config.cookie_path,
        domain=config.cookie_domain,
        max_age=max_age,
    )


def make_csrf_cookie(
    csrf_token: str,
    max_age: int,
    config: AuthConfig,
) -> Cookie:
    """Create a non-HttpOnly CSRF token cookie.

    Intentionally NOT HttpOnly so JavaScript can read the
    value and submit it as the ``X-CSRF-Token`` header
    (double-submit cookie pattern).  The path is deliberately
    broader than the session cookie's path: ``document.cookie``
    in the browser only exposes cookies whose ``Path=`` prefix
    matches the current URL, so a CSRF cookie scoped to
    ``/api`` would be invisible to JavaScript running on any
    SPA route outside ``/api`` -- breaking the header-attach
    step of the double-submit flow.

    Args:
        csrf_token: CSRF token string.
        max_age: Cookie lifetime in seconds.
        config: Auth configuration.

    Returns:
        Non-HttpOnly cookie with the CSRF token.
    """
    return Cookie(
        key=config.csrf_cookie_name,
        value=csrf_token,
        httponly=False,
        secure=config.cookie_secure,
        samesite=config.cookie_samesite,
        path=config.csrf_cookie_path,
        domain=config.cookie_domain,
        max_age=max_age,
    )


def make_refresh_cookie(
    refresh_token: str,
    max_age: int,
    config: AuthConfig,
) -> Cookie:
    """Create an HttpOnly refresh token cookie.

    Path-scoped to the refresh endpoint so the browser
    never sends it on normal API requests.

    Args:
        refresh_token: Opaque refresh token string.
        max_age: Cookie lifetime in seconds.
        config: Auth configuration.

    Returns:
        HttpOnly cookie scoped to the refresh endpoint path.
    """
    return Cookie(
        key=config.refresh_cookie_name,
        value=refresh_token,
        httponly=True,
        secure=config.cookie_secure,
        samesite=config.cookie_samesite,
        path=config.refresh_cookie_path,
        domain=config.cookie_domain,
        max_age=max_age,
    )


def make_clear_session_cookie(config: AuthConfig) -> Cookie:
    """Create a cookie that clears the session cookie.

    Args:
        config: Auth configuration.

    Returns:
        Expired cookie that instructs the browser to delete
        the session cookie.
    """
    return Cookie(
        key=config.cookie_name,
        value="",
        httponly=True,
        secure=config.cookie_secure,
        samesite=config.cookie_samesite,
        path=config.cookie_path,
        domain=config.cookie_domain,
        max_age=0,
    )


def make_clear_csrf_cookie(config: AuthConfig) -> Cookie:
    """Create a cookie that clears the CSRF cookie.

    The path **must** match the path used in :func:`make_csrf_cookie`
    (``config.csrf_cookie_path``); a ``Set-Cookie`` with a different
    path creates a *new* cookie rather than deleting the old one, and
    the stale CSRF token would continue to shadow subsequent logins
    from the same browser.

    Args:
        config: Auth configuration.

    Returns:
        Expired cookie that instructs the browser to delete
        the CSRF cookie.
    """
    return Cookie(
        key=config.csrf_cookie_name,
        value="",
        httponly=False,
        secure=config.cookie_secure,
        samesite=config.cookie_samesite,
        path=config.csrf_cookie_path,
        domain=config.cookie_domain,
        max_age=0,
    )


def make_clear_refresh_cookie(config: AuthConfig) -> Cookie:
    """Create a cookie that clears the refresh token cookie.

    Args:
        config: Auth configuration.

    Returns:
        Expired cookie that instructs the browser to delete
        the refresh cookie.
    """
    return Cookie(
        key=config.refresh_cookie_name,
        value="",
        httponly=True,
        secure=config.cookie_secure,
        samesite=config.cookie_samesite,
        path=config.refresh_cookie_path,
        domain=config.cookie_domain,
        max_age=0,
    )


def generate_csrf_token() -> str:
    """Generate a cryptographically random CSRF token.

    Returns:
        URL-safe base64 string sized by ``security.auth_token_bytes``
        (default 32 bytes / 256 bits of entropy).
    """
    return secrets.token_urlsafe(get_auth_token_bytes())
