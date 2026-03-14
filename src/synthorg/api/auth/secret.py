"""JWT secret resolution — env var → persistence → auto-generate."""

import os
import secrets

from synthorg.api.auth.config import MIN_SECRET_LENGTH
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.protocol import PersistenceBackend  # noqa: TC001

logger = get_logger(__name__)

_SETTING_KEY = "jwt_secret"
_SECRET_LENGTH = 48  # 64 URL-safe base64 chars


async def resolve_jwt_secret(
    persistence: PersistenceBackend,
) -> str:
    """Resolve the JWT signing secret using a priority chain.

    1. ``SYNTHORG_JWT_SECRET`` env var (for multi-instance deploys).
    2. Stored secret in persistence ``settings`` table.
    3. Auto-generate, persist, and return.

    Args:
        persistence: Connected persistence backend.

    Returns:
        JWT signing secret (>= 32 characters).
    """
    # 1. Env var override (highest priority)
    env_secret = os.environ.get("SYNTHORG_JWT_SECRET", "").strip()
    if env_secret:
        if len(env_secret) < MIN_SECRET_LENGTH:
            msg = (
                f"SYNTHORG_JWT_SECRET must be at least "
                f"{MIN_SECRET_LENGTH} characters (got {len(env_secret)})"
            )
            logger.error(API_APP_STARTUP, error=msg)
            raise ValueError(msg)
        logger.info(
            API_APP_STARTUP,
            note="JWT secret loaded from SYNTHORG_JWT_SECRET env var",
        )
        return env_secret

    # 2. Check persistence
    stored = await persistence.get_setting(_SETTING_KEY)
    if stored:
        stored = stored.strip()
        if len(stored) < MIN_SECRET_LENGTH:
            logger.warning(
                API_APP_STARTUP,
                note=(
                    "Stored JWT secret too short "
                    f"({len(stored)} < {MIN_SECRET_LENGTH}), "
                    "auto-generating replacement"
                ),
            )
        else:
            logger.info(
                API_APP_STARTUP,
                note="JWT secret loaded from persistence",
            )
            return stored

    # 3. Auto-generate and persist
    generated = secrets.token_urlsafe(_SECRET_LENGTH)
    await persistence.set_setting(_SETTING_KEY, generated)
    logger.info(
        API_APP_STARTUP,
        note="JWT secret auto-generated and saved to persistence",
    )
    return generated
