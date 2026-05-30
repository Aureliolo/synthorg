# module-kind: controller
"""First-run admin setup endpoint (``POST /auth/setup``)."""

import uuid
from datetime import UTC, datetime
from typing import Any

from litestar import Controller, Request, Response, post

from synthorg.api.api_core_state import (
    ApiCoreStateSlice,
    auth_service_of,
    session_store_of,
)
from synthorg.api.auth.controller_dtos import (
    CookieSessionResponse,
    SetupRequest,
)
from synthorg.api.auth.controller_helpers import (
    create_session_record,
    get_auth_config,
    make_session_cookies,
)
from synthorg.api.auth.controllers._shared import _AUTH_RATE_LIMIT
from synthorg.api.auth.service import AuthService
from synthorg.api.auth.system_user import SYSTEM_USERNAME
from synthorg.api.dto import ApiResponse
from synthorg.core.auth.models import User
from synthorg.core.auth.roles import HumanRole
from synthorg.core.domain_errors import ConflictError
from synthorg.core.persistence_errors import ConstraintViolationError
from synthorg.observability import get_logger
from synthorg.observability.events.security import (
    SECURITY_AUTH_FAILED,
    SECURITY_AUTH_SETUP_COMPLETE,
    SECURITY_SESSION_LIMIT_ENFORCED,
)
from synthorg.persistence.constraint_tokens import (
    IDX_SINGLE_CEO,
    USERS_USERNAME_UNIQUE,
)
from synthorg.persistence.state import persistence_of
from synthorg.persistence.user_protocol import UserFilterSpec

logger = get_logger(__name__)


class AuthBootstrapController(Controller):
    """First-run admin setup endpoint."""

    path = "/auth"
    tags = ("auth",)

    @post(
        "/setup",
        status_code=201,
        summary="First-run admin setup",
        middleware=[_AUTH_RATE_LIMIT.middleware],
    )
    async def setup(
        self,
        data: SetupRequest,
        request: Request[Any, Any, Any],
    ) -> Response[ApiResponse[CookieSessionResponse]]:
        """Create the first admin account (CEO).

        Only available when no users exist. Returns 409 after
        the first account is created.  The JWT is delivered via
        an HttpOnly ``Set-Cookie`` header.

        Returns:
            Result matching the declared return annotation.

        Raises:
            ConflictError: Setup is already complete (a user exists, or a
                concurrent racer won the single-CEO / unique-username guard).
            ConstraintViolationError: A persistence constraint unrelated to
                the single-CEO / unique-username guards was violated.
        """
        app_state = request.app.state["app_state"]
        auth_service: AuthService = auth_service_of(app_state)
        persistence = persistence_of(app_state)

        if data.username == SYSTEM_USERNAME:
            logger.warning(
                SECURITY_AUTH_FAILED,
                reason="setup_reserved_username",
                username=data.username,
            )
            msg = "Username 'system' is reserved"
            raise ConflictError(msg)

        # Block setup once ANY human user exists, not just a CEO: the
        # ``count`` query already excludes the SYSTEM user, so a non-zero
        # result means a real operator has been provisioned and first-run
        # setup must stay closed even if (hypothetically) no CEO row
        # remained. This is the fast-path pre-check; the atomic guarantee
        # below covers the concurrent race.
        if await persistence.users.count(UserFilterSpec()) > 0:
            logger.warning(SECURITY_AUTH_FAILED, reason="setup_already_completed")
            msg = "Setup already completed"
            raise ConflictError(msg)

        now = datetime.now(UTC)
        password_hash = await auth_service.hash_password_async(data.password)
        user = User(
            id=str(uuid.uuid4()),
            username=data.username,
            password_hash=password_hash,
            role=HumanRole.CEO,
            must_change_password=False,
            created_at=now,
            updated_at=now,
        )
        # Atomic race guard: the ``idx_single_ceo`` partial-unique index
        # (and the username-unique index) make a concurrent second setup
        # fail at the persistence layer rather than via a best-effort
        # post-write count + compensating delete, which could leave zero
        # CEOs if two requests deleted each other's row. A losing racer
        # surfaces the same "already completed" conflict the pre-check
        # raises.
        try:
            await persistence.users.save(user)
        except ConstraintViolationError as exc:
            if exc.constraint in (IDX_SINGLE_CEO, USERS_USERNAME_UNIQUE):
                logger.warning(
                    SECURITY_AUTH_FAILED,
                    reason="setup_race_detected",
                    constraint=exc.constraint,
                )
                msg = "Setup already completed"
                raise ConflictError(msg) from exc
            raise

        token, expires_in, session_id = auth_service.create_token(user)

        await create_session_record(
            request,
            app_state,
            session_id,
            user,
            expires_in,
        )

        auth_config = get_auth_config(app_state)
        if app_state.slice(ApiCoreStateSlice).session_store is not None:
            revoked = await session_store_of(app_state).enforce_session_limit(
                user.id,
                auth_config.max_concurrent_sessions,
            )
            if revoked:
                logger.info(
                    SECURITY_SESSION_LIMIT_ENFORCED,
                    user_id=user.id,
                    revoked=revoked,
                    max_sessions=auth_config.max_concurrent_sessions,
                )

        logger.info(
            SECURITY_AUTH_SETUP_COMPLETE,
            user_id=user.id,
            username=user.username,
        )

        return Response(
            content=ApiResponse(
                data=CookieSessionResponse(
                    expires_in=expires_in,
                    must_change_password=user.must_change_password,
                ),
            ),
            status_code=201,
            cookies=await make_session_cookies(
                token,
                expires_in,
                auth_config,
                app_state=app_state,
                session_id=session_id,
                user_id=user.id,
            ),
        )
