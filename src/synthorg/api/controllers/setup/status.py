# module-kind: controller
"""Setup status and template-listing endpoints.

Exposes the unauthenticated ``GET /setup/status`` probe the frontend
uses to decide whether to show the setup wizard, plus the read-only
``GET /setup/templates`` company-template listing.
"""

import asyncio

from litestar import Controller, get
from litestar.datastructures import State

from synthorg.api.controllers.setup.agent_helpers import (
    check_has_agents as _check_has_agents,
)
from synthorg.api.controllers.setup.agent_helpers import (
    check_needs_admin as _check_needs_admin,
)
from synthorg.api.controllers.setup.agent_helpers import (
    check_needs_setup as _check_needs_setup,
)
from synthorg.api.controllers.setup.company_helpers import (
    check_has_company as _check_has_company,
)
from synthorg.api.controllers.setup.company_helpers import (
    check_has_name_locales as _check_has_name_locales,
)
from synthorg.api.controllers.setup.company_helpers import (
    resolve_min_password_length as _resolve_min_password_length,
)
from synthorg.api.controllers.setup_models import (
    SetupStatusResponse,
    TemplateInfoResponse,
    TemplateVariableResponse,
)
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access
from synthorg.api.state import AppState
from synthorg.observability import get_logger
from synthorg.observability.events.setup import (
    SETUP_STATUS_CHECKED,
    SETUP_TEMPLATES_LISTED,
)
from synthorg.persistence.state import persistence_of
from synthorg.providers.state import ProvidersStateSlice
from synthorg.settings.state import settings_service_of

logger = get_logger(__name__)


class SetupStatusController(Controller):
    """First-run setup status + template-listing endpoints."""

    path = "/setup"
    tags = ("setup",)

    @get("/status")
    async def get_status(
        self,
        state: State,
    ) -> ApiResponse[SetupStatusResponse]:
        """Check whether first-run setup is needed.

        This endpoint is unauthenticated so the frontend can determine
        whether to show the setup wizard before any user exists.
        All other setup endpoints require authentication via guards.

        Args:
            state: Application state.

        Returns:
            Setup status envelope.
        """
        app_state: AppState = state.app_state
        settings_svc = settings_service_of(app_state)

        needs_admin = await _check_needs_admin(persistence_of(app_state))
        needs_setup = await _check_needs_setup(settings_svc)
        provider_registry = app_state.slice(ProvidersStateSlice).registry
        has_providers = provider_registry is not None and len(provider_registry) > 0
        async with asyncio.TaskGroup() as tg:
            co_task = tg.create_task(_check_has_company(settings_svc))
            ag_task = tg.create_task(_check_has_agents(settings_svc))
            nl_task = tg.create_task(_check_has_name_locales(settings_svc))
            pw_task = tg.create_task(
                _resolve_min_password_length(settings_svc),
            )
        has_company = co_task.result()
        has_agents = ag_task.result()
        has_name_locales = nl_task.result()
        min_password_length = pw_task.result()

        logger.debug(
            SETUP_STATUS_CHECKED,
            needs_admin=needs_admin,
            needs_setup=needs_setup,
            has_providers=has_providers,
            has_name_locales=has_name_locales,
            has_company=has_company,
            has_agents=has_agents,
        )
        return ApiResponse(
            data=SetupStatusResponse(
                needs_admin=needs_admin,
                needs_setup=needs_setup,
                has_providers=has_providers,
                has_name_locales=has_name_locales,
                has_company=has_company,
                has_agents=has_agents,
                min_password_length=min_password_length,
            ),
        )

    @get(
        "/templates",
        guards=[require_read_access],
    )
    async def get_templates(
        self,
        state: State,  # noqa: ARG002
    ) -> ApiResponse[tuple[TemplateInfoResponse, ...]]:
        """List available company templates for setup.

        Args:
            state: Application state.

        Returns:
            Template list envelope.
        """
        from synthorg.templates.loader import list_templates  # noqa: PLC0415

        templates = list_templates()
        result = tuple(
            TemplateInfoResponse(
                name=t.name,
                display_name=t.display_name,
                description=t.description,
                source=t.source,
                tags=t.tags,
                skill_patterns=t.skill_patterns,
                variables=tuple(
                    TemplateVariableResponse(
                        name=v.name,
                        description=v.description,
                        var_type=v.var_type,
                        default=v.default,
                        required=v.required,
                    )
                    for v in t.variables
                ),
                agent_count=t.agent_count,
                department_count=t.department_count,
                autonomy_level=t.autonomy_level,
                workflow=t.workflow,
            )
            for t in templates
        )

        logger.debug(SETUP_TEMPLATES_LISTED, count=len(result))
        return ApiResponse(data=result)
