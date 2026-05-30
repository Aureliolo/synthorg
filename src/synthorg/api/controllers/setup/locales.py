# module-kind: controller
"""Name-locale configuration endpoints for first-run setup.

List the available locales, read the current name-locale preference,
and save a new selection. Drives the agent name-randomisation locale
pool used by the Review Org step.
"""

import json

from litestar import Controller, get, put
from litestar.datastructures import State
from litestar.status_codes import HTTP_200_OK

from synthorg.api.controllers.setup.company_helpers import (
    check_setup_not_complete as _check_setup_not_complete,
)
from synthorg.api.controllers.setup.company_helpers import (
    read_name_locales as _read_name_locales,
)
from synthorg.api.controllers.setup.company_helpers import (
    validate_locale_selection as _validate_locale_selection,
)
from synthorg.api.controllers.setup_models import (
    AvailableLocalesResponse,
    SetupNameLocalesRequest,
    SetupNameLocalesResponse,
)
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_ceo, require_read_access
from synthorg.api.state import AppState
from synthorg.observability import get_logger
from synthorg.observability.events.setup import (
    SETUP_NAME_LOCALES_LISTED,
    SETUP_NAME_LOCALES_SAVED,
)
from synthorg.settings.state import settings_service_of

logger = get_logger(__name__)


class SetupLocalesController(Controller):
    """Name-locale configuration endpoints for the setup wizard."""

    path = "/setup"
    tags = ("setup",)

    @get(
        "/name-locales/available",
        guards=[require_read_access],
    )
    async def get_available_locales(
        self,
        state: State,  # noqa: ARG002
    ) -> ApiResponse[AvailableLocalesResponse]:
        """List available locales grouped by region.

        Args:
            state: Application state.

        Returns:
            Region-grouped locale data envelope.
        """
        from synthorg.templates.locales import (  # noqa: PLC0415
            LOCALE_DISPLAY_NAMES,
            LOCALE_REGIONS,
        )

        return ApiResponse(
            data=AvailableLocalesResponse(
                regions={k: list(v) for k, v in LOCALE_REGIONS.items()},
                display_names=dict(LOCALE_DISPLAY_NAMES),
            ),
        )

    @get(
        "/name-locales",
        guards=[require_read_access],
    )
    async def get_name_locales(
        self,
        state: State,
    ) -> ApiResponse[SetupNameLocalesResponse]:
        """Get the current name locale configuration.

        Args:
            state: Application state.

        Returns:
            Name locale envelope.
        """
        from synthorg.templates.locales import (  # noqa: PLC0415
            ALL_LOCALES_SENTINEL,
        )

        app_state: AppState = state.app_state
        settings_svc = settings_service_of(app_state)
        locales = await _read_name_locales(settings_svc, resolve=False)
        stored = locales or [ALL_LOCALES_SENTINEL]
        logger.debug(SETUP_NAME_LOCALES_LISTED, count=len(stored))
        return ApiResponse(
            data=SetupNameLocalesResponse(locales=stored),
        )

    @put(
        "/name-locales",
        guards=[require_ceo],
        status_code=HTTP_200_OK,
    )
    async def save_name_locales(
        self,
        state: State,
        data: SetupNameLocalesRequest,
    ) -> ApiResponse[SetupNameLocalesResponse]:
        """Save name locale preferences.

        Args:
            state: Application state.
            data: Locale selection payload.

        Returns:
            Saved locale envelope.
        """
        from synthorg.templates.locales import (  # noqa: PLC0415
            ALL_LOCALES_SENTINEL,
            VALID_LOCALE_CODES,
        )

        app_state: AppState = state.app_state
        settings_svc = settings_service_of(app_state)
        await _check_setup_not_complete(settings_svc)
        _validate_locale_selection(
            data.locales,
            ALL_LOCALES_SENTINEL,
            VALID_LOCALE_CODES,
        )

        await settings_svc.set(
            "company",
            "name_locales",
            json.dumps(data.locales),
        )

        logger.info(
            SETUP_NAME_LOCALES_SAVED,
            locales=data.locales,
            count=len(data.locales),
        )

        return ApiResponse(
            data=SetupNameLocalesResponse(locales=data.locales),
        )
