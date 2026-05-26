"""Team CRUD controller -- sub-resource of departments."""

import json
from typing import Annotated, Any

from litestar import Controller, delete, patch, post
from litestar.datastructures import State  # noqa: TC002
from litestar.params import QueryParameter
from litestar.status_codes import HTTP_201_CREATED, HTTP_204_NO_CONTENT
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.controllers.setup.agent_helpers import AGENT_LOCK as _AGENT_LOCK
from synthorg.api.controllers.template_packs import _read_setting_list
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_ceo_or_manager, require_read_access
from synthorg.api.path_params import PathName  # noqa: TC001
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.core.company import Team
from synthorg.core.domain_errors import ConflictError, NotFoundError, ValidationError
from synthorg.core.normalization import normalize_identifier
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_RESOURCE_CONFLICT,
    API_RESOURCE_NOT_FOUND,
    API_TEAM_CREATED,
    API_TEAM_DELETED,
    API_TEAM_REORDERED,
    API_TEAM_UPDATED,
    API_VALIDATION_FAILED,
)

logger = get_logger(__name__)


# ── DTOs ───────────────────────────────────────────────────


class CreateTeamRequest(BaseModel):
    """Request body for creating a team within a department."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    name: NotBlankStr = Field(description="Team name")
    lead: NotBlankStr = Field(description="Team lead agent name")
    members: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Team member agent names",
    )


class UpdateTeamRequest(BaseModel):
    """Request body for updating a team (partial update)."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    name: NotBlankStr | None = Field(
        default=None,
        description="New team name (rename)",
    )
    lead: NotBlankStr | None = Field(
        default=None,
        description="New team lead agent name",
    )
    members: tuple[NotBlankStr, ...] | None = Field(
        default=None,
        description="Replacement member list",
    )


class ReorderTeamsRequest(BaseModel):
    """Request body for reordering teams within a department."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    team_names: tuple[NotBlankStr, ...] = Field(
        description="Ordered team names",
    )


class TeamResponse(BaseModel):
    """Response body for a single team."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr
    lead: NotBlankStr
    members: tuple[NotBlankStr, ...]


# ── Helpers ────────────────────────────────────────────────


def _persisted_name(record: dict[str, Any], record_type: str) -> str:
    """Read the ``name`` field from a persisted record, asserting type.

    Persisted department / team records should always carry a ``str``
    ``name`` (model validation runs at write time). If a record reaches
    this layer with a non-string name, the data is corrupted: surface
    a clear validation error instead of silently coercing through
    ``str()`` and producing a misleading ``NotFoundError`` downstream.

    Args:
        record: Raw persisted dict (department or team).
        record_type: Human-readable label used in error messages
            (e.g. ``"Department"``, ``"Team"``).

    Returns:
        The ``name`` field as a ``str``.

    Raises:
        ValidationError: If ``name`` is missing or not a string.
    """
    value = record.get("name")
    if not isinstance(value, str):
        logger.warning(
            API_VALIDATION_FAILED,
            record_type=record_type,
            reason="non_string_persisted_name",
            value_type=type(value).__name__,
        )
        msg = (
            f"Persisted {record_type.lower()} record has a non-string "
            f"name (got {type(value).__name__})"
        )
        raise ValidationError(msg)
    return value


def _find_department(
    depts: list[dict[str, Any]],
    name: str,
) -> tuple[int, dict[str, Any]]:
    """Find a department by name (case-insensitive).

    Args:
        depts: Department dict list.
        name: Department name to find.

    Returns:
        Tuple of (index, department dict).

    Raises:
        NotFoundError: If not found.
        ValidationError: If a persisted record has a non-string name.
    """
    target = normalize_identifier(name)
    for idx, dept in enumerate(depts):
        if normalize_identifier(_persisted_name(dept, "Department")) == target:
            return idx, dept
    msg = f"Department {name!r} not found"
    logger.warning(
        API_RESOURCE_NOT_FOUND,
        resource="department",
        name=name,
    )
    raise NotFoundError(msg)


def _find_team(
    teams: list[dict[str, Any]],
    team_name: str,
) -> tuple[int, dict[str, Any]]:
    """Find a team by name within a department's teams list.

    Args:
        teams: Team dict list.
        team_name: Team name to find (case-insensitive).

    Returns:
        Tuple of (index, team dict).

    Raises:
        NotFoundError: If not found.
        ValidationError: If a persisted record has a non-string name.
    """
    target = normalize_identifier(team_name)
    for idx, team in enumerate(teams):
        if normalize_identifier(_persisted_name(team, "Team")) == target:
            return idx, team
    msg = f"Team {team_name!r} not found"
    logger.warning(
        API_RESOURCE_NOT_FOUND,
        resource="team",
        name=team_name,
    )
    raise NotFoundError(msg)


def _check_team_name_unique(
    teams: list[dict[str, Any]],
    name: str,
    *,
    exclude_index: int | None = None,
) -> None:
    """Raise ConflictError if a team with this name already exists.

    Args:
        teams: Team dict list.
        name: Name to check.
        exclude_index: Optional index to skip (for rename checks).

    Raises:
        ConflictError: If a name collision is detected.
        ValidationError: If a persisted record has a non-string name.
    """
    target = normalize_identifier(name)
    for idx, team in enumerate(teams):
        if idx == exclude_index:
            continue
        if normalize_identifier(_persisted_name(team, "Team")) == target:
            msg = f"Team {name!r} already exists in this department"
            logger.warning(
                API_RESOURCE_CONFLICT,
                resource="team",
                name=name,
                reason="duplicate_team_name",
            )
            raise ConflictError(msg)


def _validate_team_model(team_dict: dict[str, Any]) -> Team:
    """Validate a team dict by constructing a Team model.

    Args:
        team_dict: Raw team dict.

    Returns:
        Validated Team instance.

    Raises:
        ValidationError: If validation fails.
    """
    try:
        return Team(**team_dict)
    except (ValueError, TypeError) as exc:
        msg = f"Team validation failed: {safe_error_description(exc)}"
        logger.warning(
            API_VALIDATION_FAILED,
            reason="team_model_validation_failed",
            team_name=team_dict.get("name"),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise ValidationError(msg) from exc


def _team_to_response(team_dict: dict[str, Any]) -> TeamResponse:
    """Convert a team dict to a TeamResponse.

    Returns:
        ``TeamResponse`` instance.
    """
    return TeamResponse(
        name=team_dict["name"],
        lead=team_dict["lead"],
        members=tuple(team_dict.get("members", ())),
    )


async def _persist_departments(
    app_state: AppState,
    depts: list[dict[str, Any]],
) -> None:
    """Write the full departments list back to settings."""
    await app_state.settings_service.set(
        "company",
        "departments",
        json.dumps(depts),
    )


# ── Controller ─────────────────────────────────────────────


class TeamController(Controller):
    """Team CRUD -- sub-resource of departments."""

    path = "/departments/{dept_name:str}/teams"
    tags = ("departments",)
    guards = [require_read_access]  # noqa: RUF012

    @post(
        "/",
        status_code=HTTP_201_CREATED,
        guards=[require_ceo_or_manager],
    )
    async def create_team(
        self,
        state: State,
        dept_name: PathName,
        data: CreateTeamRequest,
    ) -> ApiResponse[TeamResponse]:
        """Create a new team within a department.

        Args:
            state: Application state.
            dept_name: Parent department name.
            data: Team creation data.

        Returns:
            Created team envelope.

        Raises:
            NotFoundError: If the department does not exist.
            ConflictError: If a team with this name already exists.
            ValidationError: If team data is invalid.
        """
        app_state: AppState = state.app_state

        async with _AGENT_LOCK:
            depts = await _read_setting_list(app_state, "departments")
            dept_idx, dept = _find_department(depts, dept_name)

            teams: list[dict[str, Any]] = list(dept.get("teams", []))
            _check_team_name_unique(teams, data.name)

            team_dict: dict[str, Any] = {
                "name": data.name,
                "lead": data.lead,
                "members": list(data.members),
            }
            _validate_team_model(team_dict)

            teams.append(team_dict)
            dept = {**dept, "teams": teams}
            depts[dept_idx] = dept

            await _persist_departments(app_state, depts)

        logger.info(
            API_TEAM_CREATED,
            department=dept_name,
            team=data.name,
        )
        return ApiResponse(data=_team_to_response(team_dict))

    @patch(
        "/reorder",
        guards=[require_ceo_or_manager],
    )
    async def reorder_teams(
        self,
        state: State,
        dept_name: PathName,
        data: ReorderTeamsRequest,
    ) -> ApiResponse[tuple[TeamResponse, ...]]:
        """Reorder teams within a department.

        The ``team_names`` must contain exactly the same set of team
        names that currently exist in the department.

        Args:
            state: Application state.
            dept_name: Parent department name.
            data: Ordered team names.

        Returns:
            Reordered teams envelope.

        Raises:
            NotFoundError: If the department does not exist.
            ValidationError: If names set does not match.
        """
        app_state: AppState = state.app_state

        async with _AGENT_LOCK:
            depts = await _read_setting_list(app_state, "departments")
            dept_idx, dept = _find_department(depts, dept_name)

            teams: list[dict[str, Any]] = list(dept.get("teams", []))
            stored_names = [_persisted_name(t, "Team") for t in teams]
            team_map: dict[str, dict[str, Any]] = {
                normalize_identifier(name): t
                for name, t in zip(stored_names, teams, strict=True)
            }
            if len(team_map) != len(teams):
                # Defense-in-depth: Department validation should reject
                # case-collision team names at write time, but if persisted
                # data ever contains them, refuse to reorder rather than
                # silently dropping the overwritten entries.
                normalized_to_originals: dict[str, list[str]] = {}
                for name in stored_names:
                    normalized_to_originals.setdefault(
                        normalize_identifier(name), []
                    ).append(name)
                colliding = sorted(
                    {
                        name
                        for originals in normalized_to_originals.values()
                        if len(originals) > 1
                        for name in originals
                    }
                )
                msg = (
                    "Cannot reorder teams: stored team names contain "
                    "case-insensitive duplicates"
                )
                logger.warning(
                    API_VALIDATION_FAILED,
                    department=dept_name,
                    reason="stored_team_name_collision",
                    stored_names=stored_names,
                    colliding=colliding,
                )
                raise ValidationError(msg)
            current_names = set(team_map)
            requested_order = [normalize_identifier(n) for n in data.team_names]
            requested_names = set(requested_order)

            if len(requested_order) != len(requested_names):
                duplicates = sorted(
                    n for n in requested_order if requested_order.count(n) > 1
                )
                msg = "team_names must not contain duplicates (case-insensitive)"
                logger.warning(
                    API_VALIDATION_FAILED,
                    department=dept_name,
                    reason="duplicate_team_names_in_request",
                    requested=list(data.team_names),
                    duplicates=sorted(set(duplicates)),
                )
                raise ValidationError(msg)

            if current_names != requested_names:
                msg = (
                    "team_names must contain exactly the current team "
                    f"names: {sorted(current_names)}"
                )
                logger.warning(
                    API_VALIDATION_FAILED,
                    department=dept_name,
                    reason="team_names_set_mismatch",
                    requested=list(data.team_names),
                    stored=sorted(current_names),
                    missing=sorted(current_names - requested_names),
                    extra=sorted(requested_names - current_names),
                )
                raise ValidationError(msg)

            reordered = [team_map[name] for name in requested_order]

            dept = {**dept, "teams": reordered}
            depts[dept_idx] = dept

            await _persist_departments(app_state, depts)

        logger.info(
            API_TEAM_REORDERED,
            department=dept_name,
            order=[str(n) for n in data.team_names],
        )
        return ApiResponse(
            data=tuple(_team_to_response(t) for t in reordered),
        )

    @patch(
        "/{team_name:str}",
        guards=[require_ceo_or_manager],
    )
    async def update_team(
        self,
        state: State,
        dept_name: PathName,
        team_name: PathName,
        data: UpdateTeamRequest,
    ) -> ApiResponse[TeamResponse]:
        """Update a team (rename, change lead, replace members).

        Only provided fields are changed; omitted fields keep their
        current values.

        Args:
            state: Application state.
            dept_name: Parent department name.
            team_name: Team to update.
            data: Partial update data.

        Returns:
            Updated team envelope.

        Raises:
            NotFoundError: If department or team not found.
            ConflictError: If rename conflicts with existing name.
            ValidationError: If updated team data is invalid.
        """
        app_state: AppState = state.app_state

        async with _AGENT_LOCK:
            depts = await _read_setting_list(app_state, "departments")
            dept_idx, dept = _find_department(depts, dept_name)

            teams: list[dict[str, Any]] = list(dept.get("teams", []))
            team_idx, team = _find_team(teams, team_name)

            updated = {**team}
            if data.name is not None:
                _check_team_name_unique(
                    teams,
                    data.name,
                    exclude_index=team_idx,
                )
                updated["name"] = data.name
            if data.lead is not None:
                updated["lead"] = data.lead
            if data.members is not None:
                updated["members"] = list(data.members)

            _validate_team_model(updated)

            teams[team_idx] = updated
            dept = {**dept, "teams": teams}
            depts[dept_idx] = dept

            await _persist_departments(app_state, depts)

        logger.info(
            API_TEAM_UPDATED,
            department=dept_name,
            team=updated["name"],
        )
        return ApiResponse(data=_team_to_response(updated))

    @delete(
        "/{team_name:str}",
        status_code=HTTP_204_NO_CONTENT,
        guards=[require_ceo_or_manager],
    )
    async def delete_team(
        self,
        state: State,
        dept_name: PathName,
        team_name: PathName,
        reassign_to: Annotated[NotBlankStr | None, QueryParameter()] = None,
    ) -> None:
        """Delete a team from a department.

        If ``reassign_to`` is provided, members of the deleted team
        are merged into the target team (deduplicated).

        Args:
            state: Application state.
            dept_name: Parent department name.
            team_name: Team to delete.
            reassign_to: Optional target team for member reassignment.

        Raises:
            NotFoundError: If department, team, or reassignment target
                not found.
            ValidationError: If reassignment produces invalid data.
        """
        app_state: AppState = state.app_state

        async with _AGENT_LOCK:
            depts = await _read_setting_list(app_state, "departments")
            dept_idx, dept = _find_department(depts, dept_name)

            teams: list[dict[str, Any]] = list(dept.get("teams", []))
            team_idx, team = _find_team(teams, team_name)

            if reassign_to is not None:
                if normalize_identifier(reassign_to) == normalize_identifier(team_name):
                    msg = "Cannot reassign members to the team being deleted"
                    logger.warning(
                        API_VALIDATION_FAILED,
                        department=dept_name,
                        reason="self_reassignment",
                        team_name=team_name,
                        reassign_to=reassign_to,
                    )
                    raise ValidationError(msg)
                target_idx, target = _find_team(teams, reassign_to)
                # Merge members (deduplicate, case-insensitive).
                existing_members = list(target.get("members", []))
                # Coerce to str so a corrupted persisted member (None, int,
                # ...) raises a 422 via _validate_team_model below rather
                # than a 500 from normalize_identifier.
                existing_lower = {
                    normalize_identifier(str(m)) for m in existing_members
                }
                for member in team.get("members", []):
                    member_normalized = normalize_identifier(str(member))
                    if member_normalized not in existing_lower:
                        existing_members.append(member)
                        existing_lower.add(member_normalized)

                updated_target = {**target, "members": existing_members}
                _validate_team_model(updated_target)
                teams[target_idx] = updated_target

            teams.pop(team_idx)
            dept = {**dept, "teams": teams}
            depts[dept_idx] = dept

            await _persist_departments(app_state, depts)

        logger.info(
            API_TEAM_DELETED,
            department=dept_name,
            team=team_name,
            reassign_to=reassign_to,
        )
