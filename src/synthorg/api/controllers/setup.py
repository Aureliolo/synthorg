"""First-run setup controller.

Exposes endpoints for the setup wizard flow: status check, template
selection, company creation, name locale configuration, agent management
(create, list, model/name update, randomize), and setup completion.
"""

import asyncio
import json
from typing import TYPE_CHECKING

from litestar import Controller, get, post, put
from litestar.datastructures import State  # noqa: TC002
from litestar.status_codes import HTTP_200_OK, HTTP_201_CREATED

if TYPE_CHECKING:
    from synthorg.settings.service import SettingsService

from synthorg.api.controllers.setup_agents import (
    agent_dict_to_summary,
    agents_to_summaries,
    build_agent_config,
    get_existing_agents,
    normalize_description,
    validate_model_assignment,
    validate_provider_and_model,
)
from synthorg.api.controllers.setup_helpers import (
    AGENT_LOCK as _AGENT_LOCK,
)
from synthorg.api.controllers.setup_helpers import (
    auto_create_template_agents as _auto_create_template_agents,
)
from synthorg.api.controllers.setup_helpers import (
    auto_select_embedder,
)
from synthorg.api.controllers.setup_helpers import (
    check_has_agents as _check_has_agents,
)
from synthorg.api.controllers.setup_helpers import (
    check_has_company as _check_has_company,
)
from synthorg.api.controllers.setup_helpers import (
    check_has_name_locales as _check_has_name_locales,
)
from synthorg.api.controllers.setup_helpers import (
    check_needs_admin as _check_needs_admin,
)
from synthorg.api.controllers.setup_helpers import (
    check_needs_setup as _check_needs_setup,
)
from synthorg.api.controllers.setup_helpers import (
    check_setup_not_complete as _check_setup_not_complete,
)
from synthorg.api.controllers.setup_helpers import (
    collect_model_ids as _collect_model_ids,
)
from synthorg.api.controllers.setup_helpers import (
    persist_company_settings as _persist_company_settings,
)
from synthorg.api.controllers.setup_helpers import (
    post_setup_reinit as _post_setup_reinit,
)
from synthorg.api.controllers.setup_helpers import (
    read_name_locales as _read_name_locales,
)
from synthorg.api.controllers.setup_helpers import (
    resolve_min_password_length as _resolve_min_password_length,
)
from synthorg.api.controllers.setup_helpers import (
    resolve_template as _resolve_template,
)
from synthorg.api.controllers.setup_helpers import (
    validate_agent_index as _validate_agent_index,
)
from synthorg.api.controllers.setup_helpers import (
    validate_locale_selection as _validate_locale_selection,
)
from synthorg.api.controllers.setup_models import (
    AvailableLocalesResponse,
    SetupAgentRequest,
    SetupAgentResponse,
    SetupAgentSummary,
    SetupCompanyRequest,
    SetupCompanyResponse,
    SetupCompleteResponse,
    SetupNameLocalesRequest,
    SetupNameLocalesResponse,
    SetupStatusResponse,
    TemplateInfoResponse,
    TemplateVariableResponse,
    UpdateAgentModelRequest,
    UpdateAgentNameRequest,
)
from synthorg.api.dto import DEFAULT_LIMIT, ApiResponse, PaginatedResponse
from synthorg.api.guards import require_ceo, require_read_access
from synthorg.api.pagination import CursorLimit, CursorParam, paginate_cursor
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.core.domain_errors import ValidationError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.setup import (
    SETUP_AGENT_CREATED,
    SETUP_AGENT_MODEL_UPDATED,
    SETUP_AGENT_NAME_RANDOMIZED,
    SETUP_AGENT_NAME_UPDATED,
    SETUP_AGENTS_AUTO_CREATED,
    SETUP_AGENTS_LISTED,
    SETUP_COMPANY_CREATED,
    SETUP_COMPLETE_CHECK_ERROR,
    SETUP_COMPLETED,
    SETUP_NAME_LOCALES_LISTED,
    SETUP_NAME_LOCALES_SAVED,
    SETUP_NO_AGENTS,
    SETUP_NO_COMPANY,
    SETUP_NO_PROVIDERS,
    SETUP_STATUS_CHECKED,
    SETUP_TEMPLATES_LISTED,
)

logger = get_logger(__name__)


async def _read_has_gpu_setting(settings_svc: SettingsService) -> bool | None:
    """Return the operator-owned ``api/setup_has_gpu`` boolean.

    Returns ``None`` on read failure (logged at WARNING with exception
    type + scrubbed description) or if the value is unparseable.
    """
    try:
        entry = await settings_svc.get("api", "setup_has_gpu")
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            SETUP_COMPLETE_CHECK_ERROR,
            check="read_has_gpu",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    raw = (entry.value or "").strip().lower()
    match raw:
        case "true" | "1" | "yes":
            return True
        case "false" | "0" | "no" | "":
            return False
        case _:
            return None


# ── Controller ───────────────────────────────────────────────


class SetupController(Controller):
    """First-run setup wizard endpoints."""

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
        settings_svc = app_state.settings_service

        needs_admin = await _check_needs_admin(app_state.persistence)
        needs_setup = await _check_needs_setup(settings_svc)
        has_providers = (
            app_state.has_provider_registry and len(app_state.provider_registry) > 0
        )
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
        settings_svc = app_state.settings_service
        await _check_setup_not_complete(settings_svc)

        tmpl_res = _resolve_template(data.template_name)
        description = normalize_description(data.description)

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
        else:
            # Blank path: clear any agents persisted by a previous
            # template selection so GET /setup/agents returns empty.
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

    @post(
        "/agent",
        status_code=HTTP_201_CREATED,
        guards=[require_ceo],
    )
    async def create_agent(
        self,
        data: SetupAgentRequest,
        state: State,
    ) -> ApiResponse[SetupAgentResponse]:
        """Create an agent during first-run setup.

        Used for the "Start Blank" path where no template is selected
        and agents are added manually from the Review Org step.

        Args:
            data: Agent creation payload.
            state: Application state.

        Returns:
            Agent creation result envelope.

        Raises:
            ConflictError: If setup has already been completed.
            NotFoundError: If the provider does not exist.
            ValidationError: If the model is not in the provider.
        """
        app_state: AppState = state.app_state
        settings_svc = app_state.settings_service
        await _check_setup_not_complete(settings_svc)

        from synthorg.templates.preset_service import (  # noqa: PLC0415
            fetch_custom_presets_map,
        )

        providers = await app_state.provider_management.list_providers()
        validate_provider_and_model(providers, data)
        custom_presets = await fetch_custom_presets_map(
            app_state.persistence.custom_presets,
        )
        agent_config = build_agent_config(
            data,
            custom_presets=custom_presets,
        )

        async with _AGENT_LOCK:
            existing_agents = await get_existing_agents(settings_svc)
            updated_agents = [*existing_agents, agent_config]
            await settings_svc.set(
                "company",
                "agents",
                json.dumps(updated_agents),
            )

        logger.info(
            SETUP_AGENT_CREATED,
            agent_name=data.name,
            role=data.role,
            provider=data.model_provider,
            model=data.model_id,
        )

        return ApiResponse(
            data=SetupAgentResponse(
                name=data.name,
                role=data.role,
                department=data.department,
                model_provider=data.model_provider,
                model_id=data.model_id,
            ),
        )

    @get(
        "/agents",
        guards=[require_read_access],
    )
    async def list_agents(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = DEFAULT_LIMIT,
    ) -> PaginatedResponse[SetupAgentSummary]:
        """List agents currently configured during setup, paginated by name.

        Used by the Review Org step to display the current org and
        allow model reassignment.

        Args:
            state: Application state.
            cursor: Opaque cursor from a previous page.
            limit: Page size.

        Returns:
            Paginated agent summaries.
        """
        app_state: AppState = state.app_state
        settings_svc = app_state.settings_service

        agents = await get_existing_agents(settings_svc)
        summaries = agents_to_summaries(agents)
        ordered = tuple(sorted(summaries, key=lambda s: s.name))

        page, meta = paginate_cursor(
            ordered,
            limit=limit,
            cursor=cursor,
            secret=app_state.cursor_secret,
        )
        logger.debug(SETUP_AGENTS_LISTED, count=len(page))
        return PaginatedResponse[SetupAgentSummary](data=page, pagination=meta)

    @put(
        "/agents/{agent_index:int}/model",
        status_code=HTTP_200_OK,
        guards=[require_ceo],
    )
    async def update_agent_model(
        self,
        agent_index: int,
        data: UpdateAgentModelRequest,
        state: State,
    ) -> ApiResponse[SetupAgentSummary]:
        """Update a single agent's model assignment during setup.

        Args:
            agent_index: Zero-based index of the agent to update.
            data: New model assignment.
            state: Application state.

        Returns:
            Updated agent summary.

        Raises:
            ConflictError: If setup has already been completed.
            NotFoundError: If the agent index is out of range.
            ValidationError: If the provider/model is invalid.
        """
        app_state: AppState = state.app_state
        settings_svc = app_state.settings_service
        await _check_setup_not_complete(settings_svc)

        # Validate provider/model before acquiring the lock.
        providers = await app_state.provider_management.list_providers()
        validate_model_assignment(providers, data)

        async with _AGENT_LOCK:
            agents = await get_existing_agents(settings_svc)
            _validate_agent_index(agent_index, agents)

            updated_agent = {
                **agents[agent_index],
                "model": {
                    "provider": data.model_provider,
                    "model_id": data.model_id,
                },
            }
            agents = [
                *agents[:agent_index],
                updated_agent,
                *agents[agent_index + 1 :],
            ]
            await settings_svc.set(
                "company",
                "agents",
                json.dumps(agents),
            )

        logger.info(
            SETUP_AGENT_MODEL_UPDATED,
            agent_index=agent_index,
            provider=data.model_provider,
            model=data.model_id,
        )

        return ApiResponse(
            data=agent_dict_to_summary(agents[agent_index]),
        )

    @put(
        "/agents/{agent_index:int}/name",
        status_code=HTTP_200_OK,
        guards=[require_ceo],
    )
    async def update_agent_name(
        self,
        agent_index: int,
        data: UpdateAgentNameRequest,
        state: State,
    ) -> ApiResponse[SetupAgentSummary]:
        """Update a single agent's display name during setup.

        Args:
            agent_index: Zero-based index of the agent to update.
            data: New name assignment.
            state: Application state.

        Returns:
            Updated agent summary.

        Raises:
            ConflictError: If setup has already been completed.
            NotFoundError: If the agent index is out of range.
        """
        app_state: AppState = state.app_state
        settings_svc = app_state.settings_service
        await _check_setup_not_complete(settings_svc)

        async with _AGENT_LOCK:
            agents = await get_existing_agents(settings_svc)
            _validate_agent_index(agent_index, agents)

            updated_agent = {
                **agents[agent_index],
                "name": data.name,
            }
            agents = [
                *agents[:agent_index],
                updated_agent,
                *agents[agent_index + 1 :],
            ]
            await settings_svc.set(
                "company",
                "agents",
                json.dumps(agents),
            )

        logger.info(
            SETUP_AGENT_NAME_UPDATED,
            agent_index=agent_index,
            name=data.name,
        )

        return ApiResponse(
            data=agent_dict_to_summary(agents[agent_index]),
        )

    @post(
        "/agents/{agent_index:int}/randomize-name",
        status_code=HTTP_200_OK,
        guards=[require_ceo],
    )
    async def randomize_agent_name(
        self,
        agent_index: int,
        state: State,
    ) -> ApiResponse[SetupAgentSummary]:
        """Generate a random name for an agent using locale preferences.

        Args:
            agent_index: Zero-based index of the agent to update.
            state: Application state.

        Returns:
            Updated agent summary with a new random name.

        Raises:
            ConflictError: If setup has already been completed.
            NotFoundError: If the agent index is out of range.
        """
        from synthorg.templates.presets import (  # noqa: PLC0415
            generate_auto_name,
        )

        app_state: AppState = state.app_state
        settings_svc = app_state.settings_service
        await _check_setup_not_complete(settings_svc)

        locales = await _read_name_locales(settings_svc)

        async with _AGENT_LOCK:
            agents = await get_existing_agents(settings_svc)
            _validate_agent_index(agent_index, agents)

            role = agents[agent_index].get("role", "Agent")
            new_name = generate_auto_name(role, locales=locales)

            updated_agent = {
                **agents[agent_index],
                "name": new_name,
            }
            agents = [
                *agents[:agent_index],
                updated_agent,
                *agents[agent_index + 1 :],
            ]
            await settings_svc.set(
                "company",
                "agents",
                json.dumps(agents),
            )

        logger.info(
            SETUP_AGENT_NAME_RANDOMIZED,
            agent_index=agent_index,
            name=new_name,
        )

        return ApiResponse(
            data=agent_dict_to_summary(agents[agent_index]),
        )

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
        settings_svc = app_state.settings_service
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
        settings_svc = app_state.settings_service
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

    @post(
        "/complete",
        guards=[
            require_ceo,
            per_op_rate_limit_from_policy("setup.complete", key="user_or_ip"),
        ],
    )
    async def complete_setup(
        self,
        state: State,
    ) -> ApiResponse[SetupCompleteResponse]:
        """Mark first-run setup as complete.

        Validates that a company and at least one provider are configured
        before allowing completion.  Agent configuration is optional
        (Quick Setup mode) -- a warning is logged when no agents exist.

        Args:
            state: Application state.

        Returns:
            Success envelope.

        Raises:
            ConflictError: If setup has already been completed.
            ValidationError: If company or providers are missing.
        """
        app_state: AppState = state.app_state
        settings_svc = app_state.settings_service
        await _check_setup_not_complete(settings_svc)

        # Verify company has been created (strict: propagate unexpected errors).
        has_company = await _check_has_company(settings_svc, strict=True)
        if not has_company:
            msg = "A company must be created before completing setup"
            logger.warning(SETUP_NO_COMPANY)
            raise ValidationError(msg)

        # Verify at least one agent exists (warn-only, not required).
        # Quick Setup mode skips agent configuration -- users add agents
        # later in Settings.
        has_agents = await _check_has_agents(settings_svc)
        if not has_agents:
            logger.info(SETUP_NO_AGENTS, note="allowed_for_quick_setup")

        # Verify at least one provider is configured.
        if not app_state.has_provider_registry or len(app_state.provider_registry) == 0:
            msg = "At least one provider must be configured before completing setup"
            logger.warning(SETUP_NO_PROVIDERS)
            raise ValidationError(msg)

        # Auto-select embedding model from configured providers.
        # Best-effort: does not block setup completion on failure.
        # The preset hint comes from the first registered provider
        # (when operators use preset names verbatim as provider names,
        # which is the wizard default); ``has_gpu`` is an operator-
        # owned boolean read from the api/setup_has_gpu setting.  Both
        # are optional hints for ``infer_deployment_tier`` and degrade
        # gracefully when absent.
        provider_names = app_state.provider_registry.list_providers()
        provider_preset_name = provider_names[0] if provider_names else None
        has_gpu = await _read_has_gpu_setting(settings_svc)
        try:
            model_ids = await _collect_model_ids(app_state)
            await auto_select_embedder(
                settings_svc=settings_svc,
                available_model_ids=model_ids,
                provider_preset_name=provider_preset_name,
                has_gpu=has_gpu,
            )
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.warning(
                SETUP_COMPLETE_CHECK_ERROR,
                check="auto_select_embedder",
            )

        await settings_svc.set("api", "setup_complete", "true")

        logger.info(SETUP_COMPLETED)

        # Re-initialize: reload providers + bootstrap agents into runtime.
        await _post_setup_reinit(app_state)

        return ApiResponse(data=SetupCompleteResponse(setup_complete=True))
