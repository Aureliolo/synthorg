"""Template packs controller -- listing and live application."""

import asyncio
import json
from typing import TYPE_CHECKING, Any, Literal

from litestar import Controller, get, post
from litestar.datastructures import State  # noqa: TC002
from litestar.status_codes import HTTP_201_CREATED
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.controllers.setup_agents import expand_template_agents
from synthorg.api.controllers.setup_helpers import AGENT_LOCK as _AGENT_LOCK
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_ceo_or_manager, require_read_access
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.budget.rebalance import RebalanceMode, compute_rebalance
from synthorg.core.domain_errors import ConflictError, DomainError, NotFoundError
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.template import (
    TEMPLATE_PACK_APPLY_DEPT_SKIPPED,
    TEMPLATE_PACK_APPLY_ERROR,
    TEMPLATE_PACK_APPLY_START,
    TEMPLATE_PACK_APPLY_SUCCESS,
    TEMPLATE_PACK_BUDGET_REBALANCED,
    TEMPLATE_PACK_BUDGET_REJECTED,
    TEMPLATE_PACK_LIST,
    TEMPLATE_PACK_SETTING_NOT_FOUND,
)
from synthorg.settings.errors import SettingNotFoundError
from synthorg.templates.errors import TemplateNotFoundError
from synthorg.templates.pack_loader import PackInfo, list_packs, load_pack

if TYPE_CHECKING:
    from collections.abc import Sequence

    from synthorg.templates.schema import TemplateDepartmentConfig

logger = get_logger(__name__)


# ---- DTOs ----------------------------------------------------------------


class PackInfoResponse(BaseModel):
    """Pack summary for the listing endpoint."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    name: NotBlankStr
    display_name: str
    description: str
    source: Literal["builtin", "user"]
    tags: tuple[str, ...]
    agent_count: int = Field(ge=0)
    department_count: int = Field(ge=0)


class ApplyTemplatePackRequest(BaseModel):
    """Request body for applying a template pack."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    pack_name: NotBlankStr = Field(description="Pack to apply")
    rebalance_mode: RebalanceMode = Field(
        default=RebalanceMode.SCALE_EXISTING,
        description="Budget rebalance strategy for existing departments",
    )


class ApplyTemplatePackResponse(BaseModel):
    """Response after applying a template pack."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    pack_name: NotBlankStr
    agents_added: int = Field(ge=0)
    departments_added: int = Field(ge=0)
    budget_before: float = Field(
        ge=0.0,
        description="Sum of existing department budgets before apply",
    )
    budget_after: float = Field(
        ge=0.0,
        description="Sum of all department budgets after apply",
    )
    rebalance_mode: RebalanceMode = Field(
        description="Rebalance strategy used",
    )
    scale_factor: float | None = Field(
        default=None,
        description="Scale factor applied to existing departments",
    )


# ---- Helpers --------------------------------------------------------------


def _pack_info_to_response(info: PackInfo) -> PackInfoResponse:
    """Convert a :class:`PackInfo` to a response DTO."""
    return PackInfoResponse(
        name=info.name,
        display_name=info.display_name,
        description=info.description,
        source=info.source,
        tags=info.tags,
        agent_count=info.agent_count,
        department_count=info.department_count,
    )


async def _read_setting_list(
    app_state: AppState,
    key: str,
) -> list[dict[str, Any]]:
    """Read a JSON list setting from the company namespace.

    Returns:
        Parsed list, or ``[]`` if the setting is missing or empty.

    Raises:
        DomainError: If the stored JSON is corrupted (invalid JSON or
            not a list of objects).
    """
    try:
        entry = await app_state.settings_service.get("company", key)
    except SettingNotFoundError:
        logger.debug(
            TEMPLATE_PACK_SETTING_NOT_FOUND,
            setting_namespace="company",
            setting_key=key,
        )
        return []
    if not entry.value:
        return []
    try:
        parsed = json.loads(entry.value)
    except json.JSONDecodeError as exc:
        logger.warning(
            TEMPLATE_PACK_APPLY_ERROR,
            key=key,
            action="corrupt_setting_json",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            exc_info=True,
        )
        msg = f"Setting 'company/{key}' contains invalid JSON"
        raise DomainError(msg) from exc
    if not isinstance(parsed, list) or not all(
        isinstance(item, dict) for item in parsed
    ):
        logger.error(
            TEMPLATE_PACK_APPLY_ERROR,
            key=key,
            action="corrupt_setting_type",
            expected="list[dict]",
            got=type(parsed).__name__,
        )
        msg = f"Setting 'company/{key}' is not a list of objects"
        raise DomainError(msg)
    return parsed


def _serialize_departments(
    pack_depts: Sequence[TemplateDepartmentConfig],
) -> list[dict[str, Any]]:
    """Serialize pack departments preserving all fields."""
    result: list[dict[str, Any]] = []
    for dept in pack_depts:
        entry: dict[str, Any] = {
            "name": dept.name,
            "budget_percent": dept.budget_percent,
        }
        if dept.head_role:
            entry["head_role"] = dept.head_role
        if dept.reporting_lines:
            entry["reporting_lines"] = list(dept.reporting_lines)
        result.append(entry)
    return result


def _deduplicate_departments(
    pack_name: str,
    pack_depts: Sequence[TemplateDepartmentConfig],
    current_depts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return pack departments that don't conflict with existing ones."""
    existing_names = {str(d.get("name", "")).lower() for d in current_depts}
    if not pack_depts:
        return []
    raw = _serialize_departments(pack_depts)
    new_depts = [d for d in raw if str(d.get("name", "")).lower() not in existing_names]
    if len(new_depts) < len(raw):
        logger.warning(
            TEMPLATE_PACK_APPLY_DEPT_SKIPPED,
            pack_name=pack_name,
            skipped=len(raw) - len(new_depts),
        )
    return new_depts


def _deduplicate_agents(
    pack_agents: list[dict[str, Any]],
    current_agents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return pack agents not already present (by name)."""
    existing = {str(a.get("name", "")).lower() for a in current_agents}
    return [a for a in pack_agents if str(a.get("name", "")).lower() not in existing]


async def _apply_pack_to_settings(
    app_state: AppState,
    data: ApplyTemplatePackRequest,
) -> ApplyTemplatePackResponse:
    """Core pack application logic under the agent lock.

    Args:
        app_state: Application state.
        data: Request with pack name.

    Returns:
        Summary of agents and departments added.

    Raises:
        NotFoundError: If the pack is not found.
    """
    try:
        loaded = await asyncio.to_thread(load_pack, data.pack_name)
    except TemplateNotFoundError as exc:
        logger.warning(
            TEMPLATE_PACK_APPLY_ERROR,
            pack_name=data.pack_name,
            error=str(exc),
        )
        msg = f"Template pack {data.pack_name!r} not found"
        raise NotFoundError(msg) from exc

    pack_agents = expand_template_agents(loaded.template)

    async with _AGENT_LOCK:
        current_agents = await _read_setting_list(app_state, "agents")
        current_depts = await _read_setting_list(app_state, "departments")

        new_agents = _deduplicate_agents(pack_agents, current_agents)
        new_depts = _deduplicate_departments(
            data.pack_name,
            loaded.template.departments,
            current_depts,
        )

        # Budget rebalancing.
        rebalance_result = compute_rebalance(
            existing_depts=current_depts,
            new_depts=new_depts,
            mode=data.rebalance_mode,
        )

        if rebalance_result.rejected:
            logger.warning(
                TEMPLATE_PACK_BUDGET_REJECTED,
                pack_name=data.pack_name,
                projected_total=rebalance_result.new_total,
            )
            msg = (
                f"Applying pack {data.pack_name!r} would push budget "
                f"total to {rebalance_result.new_total:.1f}%, exceeding 100%"
            )
            raise ConflictError(msg)

        if (
            rebalance_result.scale_factor is not None
            and rebalance_result.scale_factor < 1.0
        ):
            logger.info(
                TEMPLATE_PACK_BUDGET_REBALANCED,
                pack_name=data.pack_name,
                scale_factor=rebalance_result.scale_factor,
                old_total=rebalance_result.old_total,
                new_total=rebalance_result.new_total,
            )

        final_depts = list(rebalance_result.departments)

        settings_svc = app_state.settings_service
        await settings_svc.set(
            "company",
            "agents",
            json.dumps(current_agents + new_agents),
        )
        await settings_svc.set(
            "company",
            "departments",
            json.dumps(final_depts),
        )

    return ApplyTemplatePackResponse(
        pack_name=data.pack_name,
        agents_added=len(new_agents),
        departments_added=len(new_depts),
        budget_before=rebalance_result.old_total,
        budget_after=rebalance_result.new_total,
        rebalance_mode=data.rebalance_mode,
        scale_factor=rebalance_result.scale_factor,
    )


# ---- Controller -----------------------------------------------------------


class TemplatePackController(Controller):
    """Template pack listing and live application."""

    path = "/template-packs"
    tags = ("template-packs",)

    @get(guards=[require_read_access])
    async def list_template_packs(
        self,
    ) -> ApiResponse[tuple[PackInfoResponse, ...]]:
        """List all available template packs.

        Returns:
            Pack info envelope.
        """
        packs = await asyncio.to_thread(list_packs)
        logger.info(TEMPLATE_PACK_LIST, count=len(packs))
        return ApiResponse(
            data=tuple(_pack_info_to_response(p) for p in packs),
        )

    @post(
        "/apply",
        status_code=HTTP_201_CREATED,
        guards=[require_ceo_or_manager],
    )
    async def apply_template_pack(
        self,
        data: ApplyTemplatePackRequest,
        state: State,
    ) -> ApiResponse[ApplyTemplatePackResponse]:
        """Apply a template pack to the running organization.

        Args:
            data: Pack name.
            state: Application state.

        Returns:
            Summary of agents and departments added.

        Raises:
            NotFoundError: If the requested pack does not exist.
        """
        app_state: AppState = state.app_state
        logger.info(
            TEMPLATE_PACK_APPLY_START,
            pack_name=data.pack_name,
        )
        try:
            result = await _apply_pack_to_settings(app_state, data)
        except NotFoundError:
            raise
        except ConflictError:
            raise
        except Exception:
            logger.exception(
                TEMPLATE_PACK_APPLY_ERROR,
                pack_name=data.pack_name,
                action="apply_failed",
            )
            raise
        logger.info(
            TEMPLATE_PACK_APPLY_SUCCESS,
            pack_name=data.pack_name,
            agents_added=result.agents_added,
            departments_added=result.departments_added,
        )
        return ApiResponse(data=result)
