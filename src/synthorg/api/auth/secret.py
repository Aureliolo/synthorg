"""JWT secret resolution -- env var only, no auto-generation."""

import os

from synthorg.core.auth.config import MIN_SECRET_LENGTH
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP

logger = get_logger(__name__)

_ENV_VAR = "SYNTHORG_JWT_SECRET"


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
