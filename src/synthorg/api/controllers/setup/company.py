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
from synthorg.api.controllers.setup._embedder_setup import (
    collect_provider_models as _collect_provider_models,
)
from synthorg.api.controllers.setup._embedder_setup import (
    pick_decomposition_model_ref,
    pick_model_ref_for_tier,
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
from synthorg.api.controllers.setup._template_helpers import (
    TemplateResult,
)
from synthorg.api.controllers.setup._template_helpers import (
    resolve_template as _resolve_template,
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
from synthorg.api.controllers.setup_agents import (
    get_existing_agents,
    normalize_description,
)
from synthorg.api.controllers.setup_model_recommendations import (
    SetupModelCandidate,
    SetupModelRecommendationsResponse,
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
from synthorg.llm.model_tier_policy import tier_for_purpose
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.memory.embedding.hashing import (
    BUILTIN_EMBEDDER_MODEL,
    BUILTIN_EMBEDDER_PROVIDER,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.setup import (
    SETUP_AGENTS_AUTO_CREATED,
    SETUP_COMPANY_CREATED,
    SETUP_POSTURE_SEED_FAILED,
)
from synthorg.settings.service import SettingsService
from synthorg.settings.state import settings_service_of

logger = get_logger(__name__)


def _builtin_candidate() -> SetupModelCandidate:
    """The built-in embedder as a selectable candidate.

    Returns:
        A candidate naming the built-in provider and model.
    """
    return SetupModelCandidate(
        provider=BUILTIN_EMBEDDER_PROVIDER,
        model_id=BUILTIN_EMBEDDER_MODEL,
    )


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

    @get(
        "/model-recommendations",
        guards=[require_read_access],
    )
    async def get_model_recommendations(
        self,
        state: State,
    ) -> ApiResponse[SetupModelRecommendationsResponse]:
        """Recommend the coordinator model and enumerate embedding candidates.

        The wizard prefills the coordinator's decomposition model (a
        top-cost-tier agent's model) from these and lets the operator override
        it. The embedding list carries no recommendation: nothing ranks
        embedders, so the operator names one or memory stays off. Read-only:
        it persists nothing -- the wizard writes any choice through the
        settings API, and completion fills in only the decomposition model,
        and only when the operator left it unset.

        Args:
            state: Application state.

        Returns:
            The recommended models and the candidate lists to choose from.
        """
        app_state: AppState = state.app_state
        settings_svc = settings_service_of(app_state)
        agents = await get_existing_agents(settings_svc)
        provider_models = await _collect_provider_models(app_state)
        capable = pick_decomposition_model_ref(agents)

        def _for(purpose: PromptPurposeId) -> str | None:
            return pick_model_ref_for_tier(agents, tier_for_purpose(purpose))

        candidates = tuple(
            SetupModelCandidate(provider=provider, model_id=model_id)
            for provider, model_id in provider_models
        )
        # Recommendations come from the persisted roster, candidates from the
        # live provider configs, so an agent still assigned a since-removed
        # provider or model would prefill a ref absent from the options. The
        # picker preselects by string identity, so that renders as an empty
        # select holding an invisible value; offer no recommendation instead.
        offered = frozenset(candidate.ref for candidate in candidates)

        def _offered(ref: str | None) -> str | None:
            return ref if ref in offered else None

        return ApiResponse(
            data=SetupModelRecommendationsResponse(
                decomposition_recommended=_offered(capable),
                model_ref_candidates=candidates,
                # The built-in leads the list so an operator with no
                # embedding model still has a nameable choice, not an empty
                # picker that reads as "this step is broken".
                embedding_candidates=(_builtin_candidate(), *candidates),
                # Research reuses the capable-model heuristic (its own setting,
                # not the decomposition model). Each per-feature model is
                # recommended at its declared tier from the single tier policy.
                research_recommended=_offered(capable),
                cos_recommended=_offered(_for(PromptPurposeId.COS_CHAT)),
                propose_recommended=_offered(_for(PromptPurposeId.COS_PROPOSE)),
                routing_recommended=_offered(_for(PromptPurposeId.COS_ROUTING)),
                narrative_recommended=_offered(_for(PromptPurposeId.COS_NARRATIVE)),
                charter_recommended=_offered(_for(PromptPurposeId.CHARTER_INTERVIEW)),
            )
        )

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

        agent_summaries = await _persist_and_populate(
            app_state, settings_svc, data, tmpl_res
        )

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


def _build_render_vars(data: SetupCompanyRequest) -> dict[str, object]:
    """Build the template render variables from the company request.

    Company name + budget are dedicated company-level fields, not template
    variables, but the template Jinja still references ``{{ company_name }}`` /
    ``{{ budget }}``. Feed the real company-level values in as render variables
    alongside the genuine template-variable overrides (sprint length, WIP
    limit, ...); the company fields win over any stray var.

    Returns:
        The merged render-variable mapping.
    """
    render_vars: dict[str, object] = dict(data.template_variables)
    render_vars["company_name"] = data.company_name
    if data.budget is not None:
        render_vars["budget"] = data.budget
    return render_vars


async def _persist_and_populate(
    app_state: AppState,
    settings_svc: SettingsService,
    data: SetupCompanyRequest,
    tmpl_res: TemplateResult,
) -> tuple[SetupAgentSummary, ...]:
    """Persist the company and (re)populate its agents under ``_COMPLETE_LOCK``.

    Serialise the whole check / persist / agents-write sequence under
    ``_COMPLETE_LOCK`` so a concurrent ``/setup/complete`` (which holds the same
    lock) cannot reinit against a half-written ``company.agents`` array.
    ``_AGENT_LOCK`` is NOT acquired at this outer scope:
    ``_auto_create_template_agents`` acquires it internally and ``asyncio.Lock``
    is not reentrant, so holding it here would self-deadlock. The lock order
    across the module stays ``_COMPLETE_LOCK -> _AGENT_LOCK``.

    Returns:
        The created agent summaries (empty on the blank path).

    Raises:
        ConflictError: When setup is already complete, or a blank re-apply
            would destroy an existing roster.
    """
    async with _COMPLETE_LOCK:
        await _check_setup_not_complete(settings_svc)
        await _reject_destructive_reapply(settings_svc, tmpl_res)
        await _persist_company_settings(
            settings_svc,
            _CompanyPersist(
                company_name=data.company_name,
                description=normalize_description(data.description),
                departments_json=tmpl_res.departments_json,
                template_applied=tmpl_res.template_applied,
                currency=data.currency,
                budget=data.budget,
                model_tier_profile=data.model_tier_profile,
            ),
        )
        if tmpl_res.loaded is None or tmpl_res.template is None:
            # Blank path: clear any agents persisted by a previous template
            # selection so GET /setup/agents returns empty.
            async with _AGENT_LOCK:
                await settings_svc.set("company", "agents", "[]")
            return ()
        return await _populate_template_agents(app_state, settings_svc, data, tmpl_res)


async def _reject_destructive_reapply(
    settings_svc: SettingsService,
    tmpl_res: TemplateResult,
) -> None:
    """Reject a blank re-apply over an already-populated company.

    A template-less apply (blank) over an existing populated company would wipe
    its roster -- the resume data-loss path where a client whose template was
    not hydrated re-applies as blank and silently destroys every agent. Reject
    upfront (before any persist); regenerating from a real template, or a blank
    create on a fresh company, are both still allowed.

    Raises:
        ConflictError: When a blank apply would remove existing agents.
    """
    if tmpl_res.template is not None:
        return
    existing_agents = await get_existing_agents(settings_svc)
    if existing_agents:
        msg = (
            f"Re-applying without a template would remove the "
            f"{len(existing_agents)} existing agent(s). Select a template to "
            f"regenerate the company, or clear it first to intentionally start "
            f"blank."
        )
        raise ConflictError(msg)


async def _populate_template_agents(
    app_state: AppState,
    settings_svc: SettingsService,
    data: SetupCompanyRequest,
    tmpl_res: TemplateResult,
) -> tuple[SetupAgentSummary, ...]:
    """Auto-create the template's agents and seed its posture flags.

    Returns:
        The created agent summaries (empty when the template path is absent,
        which the caller has already excluded).
    """
    loaded = tmpl_res.loaded
    template = tmpl_res.template
    if loaded is None or template is None:
        return ()
    agent_summaries = await _auto_create_template_agents(
        loaded,
        app_state,
        settings_svc,
        variables=_build_render_vars(data),
        tier_profile=data.model_tier_profile,
    )
    logger.info(
        SETUP_AGENTS_AUTO_CREATED,
        count=len(agent_summaries),
        template=tmpl_res.template_applied,
    )
    # Seed the template posture's settings-resident feature flags before
    # /setup/complete runs post_setup_reinit, so the rebuilt runtime and boot
    # wiring pick them up. Non-fatal: the company and agents are already
    # persisted, so a seed failure logs a WARNING and lets setup succeed (the
    # operator re-applies the posture) rather than aborting under the lock.
    try:
        await _seed_posture_settings(settings_svc, template)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            SETUP_POSTURE_SEED_FAILED,
            template=tmpl_res.template_applied,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
    return agent_summaries
