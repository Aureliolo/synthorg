# module-kind: controller
"""Restart controller -- applies a restart-required setting from the dashboard.

A setting marked ``restart_required`` is read once at boot, so saving it
changes nothing until the process comes back. Without an in-app control the
operator has to leave the product and find a shell, which makes a documented,
supported setting effectively unreachable through the interface that offers it.

Restarting is exiting: this process shuts down and something outside it starts
a new one. That "something" is the container restart policy the shipped compose
file sets on every service. Where nothing is watching, exiting would take the
deployment down and leave it down, so the endpoint refuses rather than trusting
the caller to know which kind of deployment they are on.
"""

import asyncio
import os
import signal
from typing import Final

from litestar import Controller, post
from litestar.datastructures import State
from litestar.status_codes import HTTP_202_ACCEPTED
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_roles
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.core.auth.roles import HumanRole
from synthorg.core.domain_errors import ConflictError, ValidationError
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_APP_RESTART_REFUSED,
    API_APP_RESTART_REQUESTED,
)

logger = get_logger(__name__)

# Long enough for the 202 to be written and flushed to the client before the
# signal lands, short enough that the operator is not left watching a button.
# The dashboard starts polling for the new process immediately, so this only
# has to outlast the response write, not the shutdown itself.
_SIGNAL_DELAY_SECONDS: Final[float] = 0.5

_UNSUPERVISED_MESSAGE: Final[str] = (
    "Nothing is configured to restart this process, so exiting would stop the"
    " deployment and leave it stopped. Set api.restart_supervised once the"
    " process runs under a restart policy (the shipped compose file sets one),"
    " or restart it the way this deployment is started."
)


class RestartRequest(BaseModel):
    """Body for a restart request.

    ``confirm`` is the whole body on purpose: a restart is not parameterised,
    and the field exists so the destructive call cannot be made by a bare POST
    to a guessed path. No free-text reason is collected -- the audit trail
    carries who and when, which is what an operator is later asked to account
    for, and a required justification box only teaches people to type "test".
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    confirm: bool = Field(description="Must be true to proceed")


class RestartResponse(BaseModel):
    """What the caller gets back before the process goes away.

    Attributes:
        restarting: Always true; the refusal paths raise instead.
        delay_seconds: How long the process waits before signalling itself,
            so the client knows when to start looking for the new one.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    restarting: bool = Field(description="Whether a restart was scheduled")
    delay_seconds: float = Field(
        ge=0.0,
        description="Seconds until this process signals itself to shut down",
    )


def _signal_self() -> None:
    """Ask this process to shut down the way its supervisor would.

    ``SIGTERM`` rather than an immediate exit so the ASGI server runs the
    normal shutdown path: in-flight requests drain and every service's stop
    hook fires, exactly as they do for a ``docker stop``. Killing the process
    outright would skip all of it and leave whatever those hooks flush unsaved.
    """
    os.kill(os.getpid(), signal.SIGTERM)


class RestartController(Controller):
    """Admin endpoint that restarts the process to apply pending settings."""

    path = "/meta/restart"
    # Tagged by where it lives, not by who may call it: the endpoint table
    # groups by tag and requires a tag's paths to share a base, so claiming
    # ``admin`` here would split that tag across two unrelated trees. The
    # admin restriction is enforced by the role guard below, not by a label.
    tags = ("meta",)
    guards = [require_roles(HumanRole.CEO, HumanRole.SYSTEM)]  # noqa: RUF012

    @post(
        "",
        status_code=HTTP_202_ACCEPTED,
        guards=[
            per_op_rate_limit_from_policy("admin.restart", key="user"),
        ],
    )
    async def restart(
        self,
        state: State,
        data: RestartRequest,
    ) -> ApiResponse[RestartResponse]:
        """Schedule a graceful restart of this process.

        Args:
            state: Application state.
            data: Restart request carrying the confirmation flag.

        Returns:
            Acknowledgement, sent before the process signals itself.

        Raises:
            ValidationError: When ``confirm`` is not true (422).
            ConflictError: When nothing would restart the process (409).
        """
        if not data.confirm:
            msg = "confirm must be true to restart"
            raise ValidationError(msg)
        app_state: AppState = state.app_state
        # Deferred for the cold-import reason ``settings.feature_gate``
        # documents: the resolver pulls a heavy subgraph that a controller
        # import would drag into module-import time.
        from synthorg.settings.state import config_resolver_of  # noqa: PLC0415

        supervised = await config_resolver_of(app_state).get_bool(
            "api",
            "restart_supervised",
        )
        if not supervised:
            logger.warning(API_APP_RESTART_REFUSED, reason="unsupervised")
            raise ConflictError(_UNSUPERVISED_MESSAGE)
        logger.info(API_APP_RESTART_REQUESTED, delay_seconds=_SIGNAL_DELAY_SECONDS)
        asyncio.get_running_loop().call_later(_SIGNAL_DELAY_SECONDS, _signal_self)
        return ApiResponse(
            data=RestartResponse(
                restarting=True,
                delay_seconds=_SIGNAL_DELAY_SECONDS,
            )
        )
