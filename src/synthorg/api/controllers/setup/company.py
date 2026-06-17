# module-kind: controller
"""Company-creation endpoint for first-run setup.

Persists company name, description, departments, and -- when a template
is selected -- auto-creates the template's agents with model assignments
matched to the configured provider(s).
"""

from litestar import Controller, post
from litestar.datastructures import State
from litestar.status_codes import HTTP_201_CREATED

from synthorg.api.controllers.setup._embedder_setup import (
    auto_create_template_agents as _auto_create_template_agents,
)
from synthorg.api.controllers.setup._posture_seeding import (
    seed_posture_settings as _seed_posture_settings,
)
from synthorg.api.controllers.setup._runtime_wiring import (
    AGENT_LOCK as _AGENT_LOCK,
)
from synthorg.api.controllers.setup._runtime_wiring import (
    COMPLETE_LOCK as _COMPLETE_LOCK,
)
from synthorg.api.controllers.setup.company_helpers import (
    check_setup_not_complete as _check_setup_not_complete,
)
from synthorg.api.controllers.setup.company_helpers import (
    persist_company_settings as _persist_company_settings,
)
from synthorg.api.controllers.setup.company_helpers import (
    resolve_template as _resolve_template,
)
from synthorg.api.controllers.setup_agents import (
    normalize_description,
)
from synthorg.api.controllers.setup_models import (
    SetupAgentSummary,
    SetupCompanyRequest,
    SetupCompanyResponse,
)
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_ceo
from synthorg.api.state import AppState
from synthorg.observability import get_logger
from synthorg.observability.events.setup import (
    SETUP_AGENTS_AUTO_CREATED,
    SETUP_COMPANY_CREATED,
)
from synthorg.settings.state import settings_service_of

logger = get_logger(__name__)


class SetupCompanyController(Controller):
    """Company-creation endpoint for the setup wizard."""

    path = "/setup"
    tags = ("setup",)

    @post(
        "/company",
        status_code=HTTP_201_CREATED,
        guards=[require_ceo],
    )
    async def create_company(
        self,
        data: SetupCompanyRequest,
        state: State,
    ) -> ApiResponse[SetupCompanyResponse]:
        """Create company configuration during first-run setup.

        Persists company name, description, departments, and -- when a
        template is selected -- auto-creates all template agents with
        model assignments matched to the configured provider(s).

        Args:
            data: Company creation payload.
            state: Application state.

        Returns:
            Company creation result envelope.

        Raises:
            ConflictError: If setup has already been completed.
        """
        app_state: AppState = state.app_state
        settings_svc = settings_service_of(app_state)

        tmpl_res = _resolve_template(data.template_name)
        description = normalize_description(data.description)

        # Serialise the whole check / persist / agents-write sequence
        # under _COMPLETE_LOCK so a concurrent ``/setup/complete``
        # (which holds the same lock) cannot reinit against a
        # half-written ``company.agents`` array. _AGENT_LOCK is NOT
        # acquired at this outer scope: ``_auto_create_template_agents``
        # acquires _AGENT_LOCK internally and ``asyncio.Lock`` is not
        # reentrant, so holding it here would self-deadlock. The
        # leaf agents-writes take _AGENT_LOCK at their own narrow scope
        # instead -- the lock order across the module stays
        # _COMPLETE_LOCK -> _AGENT_LOCK.
        async with _COMPLETE_LOCK:
            await _check_setup_not_complete(settings_svc)
            await _persist_company_settings(
                settings_svc,
                data.company_name,
                description,
                tmpl_res.departments_json,
            )

            agent_summaries: tuple[SetupAgentSummary, ...] = ()
            if tmpl_res.template is not None:
                agent_summaries = await _auto_create_template_agents(
                    tmpl_res.template,
                    app_state,
                    settings_svc,
                )
                logger.info(
                    SETUP_AGENTS_AUTO_CREATED,
                    count=len(agent_summaries),
                    template=tmpl_res.template_applied,
                )
                # Seed the template posture's settings-resident feature flags
                # before /setup/complete runs post_setup_reinit, so the
                # rebuilt runtime and boot wiring pick them up.
                await _seed_posture_settings(settings_svc, tmpl_res.template)
            else:
                # Blank path: clear any agents persisted by a previous
                # template selection so GET /setup/agents returns empty.
                async with _AGENT_LOCK:
                    await settings_svc.set("company", "agents", "[]")

        logger.info(
            SETUP_COMPANY_CREATED,
            company_name=data.company_name,
            description_present=description is not None,
            template=tmpl_res.template_applied,
            department_count=tmpl_res.department_count,
            agent_count=len(agent_summaries),
        )
        return ApiResponse(
            data=SetupCompanyResponse(
                company_name=data.company_name,
                description=description,
                template_applied=tmpl_res.template_applied,
                department_count=tmpl_res.department_count,
                agents=agent_summaries,
            ),
        )
