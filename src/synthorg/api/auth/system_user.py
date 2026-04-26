"""System user -- persistent internal identity for CLI-to-backend auth.

The system user is bootstrapped at application startup and serves as
the JWT subject for CLI commands (``synthorg backup``, ``synthorg wipe``,
etc.).  It cannot be logged into or modified through the API.  Delete
protection is enforced at the repository layer.
"""

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from synthorg.api.auth.models import User
from synthorg.api.guards import HumanRole
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_AUTH_SYSTEM_USER_ENSURED

if TYPE_CHECKING:
    from synthorg.api.auth.service import AuthService
    from synthorg.persistence.protocol import PersistenceBackend

logger = get_logger(__name__)

# The ID and username are deliberately identical.  Security gates
# use the ID (via ``is_system_user``), not the username.
# Cross-language: the Go CLI (cli/cmd/backup.go) hard-codes
# sub="system" in the JWT payload -- keep in sync.
SYSTEM_USER_ID: Final[str] = "system"
SYSTEM_USERNAME: Final[str] = "system"

# Issuer claim required in CLI JWTs that skip pwd_sig validation.
# Cross-language: the Go CLI (cli/cmd/backup.go) hard-codes
# iss="synthorg-cli" -- keep in sync.
SYSTEM_ISSUER: Final[str] = "synthorg-cli"

# Audience claim included in CLI JWTs for defense-in-depth.
# Cross-language: the Go CLI (cli/cmd/backup.go) hard-codes
# aud="synthorg-backend" -- keep in sync.
SYSTEM_AUDIENCE: Final[str] = "synthorg-backend"

# Issuer / audience for human user JWTs minted by the API. Distinct
# from the system pair so a leaked CLI token cannot be replayed as a
# user token (or vice versa) -- both iss and aud are validated in the
# auth middleware. The API mints user tokens itself, so there is no
# cross-language coordination here.
USER_ISSUER: Final[str] = "synthorg-api"
USER_AUDIENCE: Final[str] = "synthorg-api"


def is_system_user(user_id: str) -> bool:
    """Check whether *user_id* is the system user.

    Args:
        user_id: User identifier to check.

    Returns:
        ``True`` if *user_id* matches the well-known system user ID.
    """
    return user_id == SYSTEM_USER_ID


async def ensure_system_user(
    persistence: PersistenceBackend,
    auth_service: AuthService,
) -> None:
    """Create the system user if it does not already exist.

    Safe to call on every startup.  If the user already exists,
    returns immediately.  Under concurrent startup a narrow TOCTOU
    window exists between the existence check and the save; the
    underlying persistence backend's upsert semantics ensure this
    is harmless -- the last writer wins, regenerating the password
    hash.  This is safe because CLI tokens authenticate via the
    shared JWT HMAC signature and skip ``pwd_sig`` validation
    (enforced in ``_resolve_jwt_user``).

    The system user receives a random Argon2id password hash
    generated from a 128-character hex string derived from 64
    bytes of ``os.urandom``.  Nobody knows the plaintext,
    preventing login via ``/auth/login``.

    Args:
        persistence: Connected persistence backend.
        auth_service: Authentication service for password hashing.
    """
    existing = await persistence.users.get(SYSTEM_USER_ID)
    if existing is not None:
        logger.debug(
            API_AUTH_SYSTEM_USER_ENSURED,
            action="already_exists",
            user_id=SYSTEM_USER_ID,
        )
        return

    # Generate a random password nobody will ever know.
    random_password = os.urandom(64).hex()
    password_hash = await auth_service.hash_password_async(random_password)

    now = datetime.now(UTC)
    user = User(
        id=SYSTEM_USER_ID,
        username=SYSTEM_USERNAME,
        password_hash=password_hash,
        role=HumanRole.SYSTEM,
        must_change_password=False,
        created_at=now,
        updated_at=now,
    )
    await persistence.users.save(user)
    logger.info(
        API_AUTH_SYSTEM_USER_ENSURED,
        action="created",
        user_id=SYSTEM_USER_ID,
    )
