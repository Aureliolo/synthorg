# module-kind: controller
"""Company-creation endpoint for first-run setup.

Persists company name, description, departments, and -- when a template
is selected -- auto-creates the template's agents with model assignments
matched to the configured provider(s).
"""

import asyncio

from litestar import Controller, get, post
from litestar.datastructures import State
from litestar.status_codes import HTTP_201_CREATED

from synthorg.api.controllers.setup._company_read import (
    build_company_response as _build_company_response,
)
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
    CompanyPersist as _CompanyPersist,
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
    get_existing_agents,
    normalize_description,
)
from synthorg.api.controllers.setup_models import (
    SetupAgentSummary,
    SetupCompanyRequest,
    SetupCompanyResponse,
)
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_ceo, require_read_access
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ConflictError, NotFoundError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.setup import (
    SETUP_AGENTS_AUTO_CREATED,
    SETUP_COMPANY_CREATED,
    SETUP_POSTURE_SEED_FAILED,
)
from synthorg.settings.state import settings_service_of

logger = get_logger(__name__)


class SetupCompanyController(Controller):
    """Company-creation endpoint for the setup wizard."""

    path = "/setup"
    tags = ("setup",)

    @get(
        "/company",
        guards=[require_read_access],
    )
    async def get_company(
        self,
        state: State,
    ) -> ApiResponse[SetupCompanyResponse]:
        """Return the persisted company so any client can rehydrate on resume.

        The wizard holds no client-side company copy; it hydrates from here.
        Rebuilds the same ``SetupCompanyResponse`` shape ``POST /setup/company``
        returns, from the ``company.*`` settings.

        Args:
            state: Application state.

        Returns:
            The persisted company configuration envelope.

        Raises:
            NotFoundError: When no company has been created yet.
        """
        app_state: AppState = state.app_state
        settings_svc = settings_service_of(app_state)
        response = await _build_company_response(settings_svc)
        if response is None:
            msg = "No company has been created yet"
            raise NotFoundError(msg)
        return ApiResponse(data=response)

    @post(
        "/company",
        status_code=HTTP_201_CREATED,
        guards=[
            require_ceo,
            per_op_rate_limit_from_policy("setup.company", key="user_or_ip"),
        ],
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

        # ``_resolve_template`` loads and renders the selected company
        # template from disk (YAML parse + inheritance walk); offload the
        # blocking file I/O so it does not stall the event loop.
        tmpl_res = await asyncio.to_thread(_resolve_template, data.template_name)
        description = normalize_description(data.description)

        # Company name + budget are dedicated company-level fields, not template
        # variables, but the template Jinja still references {{ company_name }}
        # / {{ budget }}. Feed the real company-level values in as render
        # variables alongside the genuine template-variable overrides (sprint
        # length, WIP limit, ...); the company fields win over any stray var.
        render_vars: dict[str, object] = dict(data.template_variables)
        render_vars["company_name"] = data.company_name
        if data.budget is not None:
            render_vars["budget"] = data.budget

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
            # Guard the destructive re-apply: a template-less apply (blank) over
            # an existing populated company would wipe its roster. That is the
            # resume data-loss path -- a client whose template was not hydrated
            # re-applies as blank and silently destroys every agent. Reject
            # upfront (before any persist) so neither the SPA nor a raw API
            # caller can lose data; regenerating from a real template, or a
            # blank create on a fresh company, are both still allowed.
            if tmpl_res.template is None:
                existing_agents = await get_existing_agents(settings_svc)
                if existing_agents:
                    msg = (
                        f"Re-applying without a template would remove the "
                        f"{len(existing_agents)} existing agent(s). Select a "
                        f"template to regenerate the company, or clear it first "
                        f"to intentionally start blank."
                    )
                    raise ConflictError(msg)
            await _persist_company_settings(
                settings_svc,
                _CompanyPersist(
                    company_name=data.company_name,
                    description=description,
                    departments_json=tmpl_res.departments_json,
                    template_applied=tmpl_res.template_applied,
                    currency=data.currency,
                    budget=data.budget,
                    model_tier_profile=data.model_tier_profile,
                ),
            )

            agent_summaries: tuple[SetupAgentSummary, ...] = ()
            if tmpl_res.loaded is not None and tmpl_res.template is not None:
                agent_summaries = await _auto_create_template_agents(
                    tmpl_res.loaded,
                    app_state,
                    settings_svc,
                    variables=render_vars,
                )
                logger.info(
                    SETUP_AGENTS_AUTO_CREATED,
                    count=len(agent_summaries),
                    template=tmpl_res.template_applied,
                )
                # Seed the template posture's settings-resident feature flags
                # before /setup/complete runs post_setup_reinit, so the
                # rebuilt runtime and boot wiring pick them up. Non-fatal:
                # the company and agents are already persisted, so a seed
                # failure logs a WARNING and lets setup succeed (the operator
                # re-applies the posture) rather than aborting under the lock.
                try:
                    await _seed_posture_settings(settings_svc, tmpl_res.template)
                except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                    reraise_critical(exc)
                    logger.warning(
                        SETUP_POSTURE_SEED_FAILED,
                        template=tmpl_res.template_applied,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
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
                currency=data.currency,
                budget=data.budget,
                model_tier_profile=data.model_tier_profile,
                agents=agent_summaries,
            ),
        )
