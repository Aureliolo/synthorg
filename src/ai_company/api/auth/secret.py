"""JWT secret resolution — env var → persistence → auto-generate."""

import os
import secrets

from ai_company.observability import get_logger
from ai_company.observability.events.api import API_APP_STARTUP
from ai_company.persistence.protocol import PersistenceBackend  # noqa: TC001

logger = get_logger(__name__)

_SETTING_KEY = "jwt_secret"
_SECRET_LENGTH = 48  # 64 URL-safe base64 chars


async def resolve_jwt_secret(
    persistence: PersistenceBackend,
) -> str:
    """Resolve the JWT signing secret using a priority chain.

    1. ``AI_COMPANY_JWT_SECRET`` env var (for multi-instance deploys).
    2. Stored secret in persistence ``settings`` table.
    3. Auto-generate, persist, and return.

    Args:
        persistence: Connected persistence backend.

    Returns:
        JWT signing secret (>= 32 characters).
    """
    # 1. Env var override (highest priority)
    env_secret = os.environ.get("AI_COMPANY_JWT_SECRET", "").strip()
    if env_secret:
        logger.info(
            API_APP_STARTUP,
            note="JWT secret loaded from AI_COMPANY_JWT_SECRET env var",
        )
        return env_secret

    # 2. Check persistence
    stored = await persistence.get_setting(_SETTING_KEY)
    if stored:
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
