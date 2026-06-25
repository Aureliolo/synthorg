"""JWT secret resolution -- env var only, no auto-generation."""

import os

from synthorg.core.auth.config import MIN_SECRET_LENGTH
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP

logger = get_logger(__name__)

_ENV_VAR = "SYNTHORG_JWT_SECRET"
_DEV_BYPASS_ENV_VAR = "SYNTHORG_DEV_AUTH_BYPASS"
_TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on"})


def resolve_dev_auth_bypass() -> bool:
    """Resolve the dev auth-bypass flag from the environment.

    DEV ONLY. When enabled, the gated ``POST /auth/dev-login`` endpoint mints a
    real admin session with no password (see :class:`AuthConfig`). Defaults
    ``False``; MUST never be enabled in production. This module is the bootstrap
    env-read allowlist; the caller logs the security warning once when enabled.

    Returns:
        ``True`` when ``SYNTHORG_DEV_AUTH_BYPASS`` is set to a truthy value.
    """
    raw = os.environ.get(_DEV_BYPASS_ENV_VAR)
    return raw is not None and raw.strip().lower() in _TRUTHY


def resolve_jwt_secret() -> str:
    """Resolve the JWT signing secret from the environment variable.

    The secret must be set explicitly via ``SYNTHORG_JWT_SECRET``.
    ``synthorg init`` generates one automatically during setup.

    Returns:
        JWT signing secret (>= 32 characters).

    Raises:
        ValueError: If the env var is not set, empty, or too short.
    """
    raw_or_none = os.environ.get(_ENV_VAR)
    if raw_or_none is None:
        msg = (
            f"{_ENV_VAR} is not set -- the JWT secret is required. "
            f"Run 'synthorg init' to generate one, or set it manually "
            f"(>= {MIN_SECRET_LENGTH} characters)."
        )
        logger.error(API_APP_STARTUP, error=msg)
        raise ValueError(msg)
    raw = raw_or_none.strip()
    if not raw:
        msg = (
            f"{_ENV_VAR} is set but empty -- "
            f"provide a value >= {MIN_SECRET_LENGTH} characters"
        )
        logger.error(API_APP_STARTUP, error=msg)
        raise ValueError(msg)

    if len(raw) < MIN_SECRET_LENGTH:
        msg = (
            f"{_ENV_VAR} must be at least "
            f"{MIN_SECRET_LENGTH} characters (got {len(raw)})"
        )
        logger.error(API_APP_STARTUP, error=msg)
        raise ValueError(msg)

    logger.info(
        API_APP_STARTUP,
        note="JWT secret loaded from SYNTHORG_JWT_SECRET env var",
    )
    return raw
