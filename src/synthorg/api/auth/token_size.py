"""Process-wide token-size cache for the auth surface.

Every URL-safe secret minted across the auth surface (WebSocket
tickets, password-reset tokens, refresh tokens, OAuth state tokens)
shares the same entropy budget so an operator cannot accidentally
weaken one path while hardening another.  The shared budget resolves
through the standard precedence chain at process startup -- the
:data:`security.auth_token_bytes` setting is ``restart_required=True``
**and** ``read_only_post_init=True``: operators cannot change it
through the /settings API at runtime; updates require an env / YAML
change followed by a process restart.  This is enforced because
changing token byte length mid-run would silently invalidate existing
tokens (a 32-byte token decoded under a 64-byte expectation fails
verification).

The startup hook in :mod:`synthorg.api.lifecycle_helpers` calls
:func:`set_auth_token_bytes` once with the resolved value before the
first request is served.  Tests that need a different value can call
the same setter directly.
"""

_DEFAULT_AUTH_TOKEN_BYTES: int = 32
"""Fallback entropy budget when the resolver is unavailable.

Mirrors the registry default for ``security.auth_token_bytes``.
Thirty-two bytes resolves to 256 bits of entropy and 43 URL-safe
base64 characters per :func:`secrets.token_urlsafe`.
"""

_MIN_AUTH_TOKEN_BYTES: int = 16
"""Lower bound enforced by :func:`set_auth_token_bytes`.

Sixteen bytes is 128 bits of entropy -- the minimum for a session
token where birthday-collision resistance still meets the 2^64
attacker bound. Anything weaker is rejected at boot.
"""

_MAX_AUTH_TOKEN_BYTES: int = 64
"""Upper bound; matches the registry max_value."""


_token_bytes: int = _DEFAULT_AUTH_TOKEN_BYTES


def get_auth_token_bytes() -> int:
    """Return the current process-wide auth token entropy budget.

    Returns the registered default until the lifecycle hook calls
    :func:`set_auth_token_bytes`.  Test harnesses that bypass the
    hook see the default value.

    Returns:
        Resulting integer.
    """
    return _token_bytes


def set_auth_token_bytes(value: int) -> None:
    """Set the process-wide entropy budget for auth-surface tokens.

    Called from :mod:`synthorg.api.lifecycle_helpers` once at startup
    after resolving ``security.auth_token_bytes``.  Subsequent calls
    overwrite the value (test harnesses), so the function is
    idempotent in practice.

    Raises:
        ValueError: If *value* is outside ``[16, 64]``.
    """
    if not _MIN_AUTH_TOKEN_BYTES <= value <= _MAX_AUTH_TOKEN_BYTES:
        msg = (
            f"auth_token_bytes={value} out of range"
            f" [{_MIN_AUTH_TOKEN_BYTES}, {_MAX_AUTH_TOKEN_BYTES}]"
        )
        raise ValueError(msg)
    global _token_bytes  # noqa: PLW0603
    _token_bytes = value
