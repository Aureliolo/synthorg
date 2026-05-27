"""Charter feature state slice.

Holds the charter interview backend and approval dispatcher. Both are
``None`` until wired at boot (interview needs a provider + persistence;
the dispatcher additionally needs the work-pipeline spine, cost-forecast
store, and budget config). Controllers read this slice and raise 503 on a
``None`` field, preserving the historic ``has_charter_service`` semantics.
"""

from typing import TYPE_CHECKING

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg.meta.charter.dispatch import CharterDispatcher  # noqa: TC001
from synthorg.meta.charter.service import (
    CharterInterviewService,  # noqa: TC001
)

if TYPE_CHECKING:
    from synthorg.api.state_slices import AppStateSliceMixin


class CharterStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the charter feature."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    interview_service: CharterInterviewService | None = None
    dispatcher: CharterDispatcher | None = None


def charter_service_of(app_state: AppStateSliceMixin) -> CharterInterviewService:
    """Resolve the charter interview service from its slice, or raise 503.

    Returns:
        The wired charter interview service.
    """
    return require_service(
        app_state.slice(CharterStateSlice).interview_service, "Charter Service"
    )


def charter_dispatcher_of(app_state: AppStateSliceMixin) -> CharterDispatcher:
    """Resolve the charter dispatcher from its slice, or raise 503.

    Returns:
        The wired charter dispatcher.
    """
    return require_service(
        app_state.slice(CharterStateSlice).dispatcher, "Charter Dispatcher"
    )
