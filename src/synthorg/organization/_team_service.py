# module-kind: service
"""Settings-backed team CRUD for the organization MCP surface.

Teams are sub-documents of ``company.departments[*].teams`` (the same durable,
dashboard-visible structure the REST ``TeamController`` mutates), so
``TeamService`` reads and writes them through the shared settings path under
:data:`~synthorg.organization.settings_write_lock.ORG_SETTINGS_WRITE_LOCK`. Each
team is addressed by its ``(department, name)`` pair; there is no separate
durable team store or team id.
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING

from synthorg.core.domain_errors import NotFoundError
from synthorg.core.normalization import normalize_identifier
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.company import (
    TEAM_CREATED_VIA_MCP,
    TEAM_DELETED_VIA_MCP,
    TEAM_UPDATED_VIA_MCP,
)
from synthorg.organization.settings_write_lock import ORG_SETTINGS_WRITE_LOCK
from synthorg.organization.team_navigation import (
    check_team_name_unique,
    find_department,
    find_team,
    persist_company_departments,
    persisted_name,
    read_company_departments,
    teams_of,
    validate_team_model,
)

if TYPE_CHECKING:
    from synthorg.api.state_slices import AppStateSliceMixin

logger = get_logger(__name__)


def _team_view(*, department: str, team: dict[str, object]) -> dict[str, object]:
    """Project a persisted team dict + its department into a stable MCP view.

    Returns:
        A JSON-safe mapping of ``department`` + validated ``name`` / ``lead`` /
        ``members``.
    """
    model = validate_team_model(team)
    return {
        "department": department,
        "name": model.name,
        "lead": model.lead,
        "members": list(model.members),
    }


class TeamService:
    """Settings-backed team CRUD over ``company.departments[*].teams``.

    Teams are addressed by ``(department, name)``. Reads project the durable
    settings structure; writes take the shared company-structure lock so they
    cannot lose updates against the REST setup / team controllers.
    """

    def __init__(self, *, app_state: AppStateSliceMixin) -> None:
        self._app_state = app_state

    async def list_teams(
        self,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[tuple[dict[str, object], ...], int]:
        """Return a paginated slice of every team plus the unfiltered total.

        Teams are ordered by ``(department, name)`` case-insensitively so
        pagination is deterministic across calls.

        Returns:
            A ``(page, total)`` pair of department-tagged team views.

        Raises:
            ValueError: If ``offset`` is negative, or ``limit`` is provided
                and non-positive.
        """
        if offset < 0:
            msg = f"offset must be >= 0, got {offset}"
            raise ValueError(msg)
        if limit is not None and limit < 1:
            msg = f"limit must be >= 1 when provided, got {limit}"
            raise ValueError(msg)
        depts = await read_company_departments(self._app_state)
        views: list[dict[str, object]] = []
        for dept in depts:
            dept_name = persisted_name(dept, "Department")
            views.extend(
                _team_view(department=dept_name, team=team) for team in teams_of(dept)
            )
        views.sort(
            key=lambda view: (
                normalize_identifier(str(view["department"])),
                normalize_identifier(str(view["name"])),
            )
        )
        total = len(views)
        end = total if limit is None else offset + limit
        return tuple(views[offset:end]), total

    async def get_team(
        self,
        *,
        department: NotBlankStr,
        team_name: NotBlankStr,
    ) -> dict[str, object] | None:
        """Fetch a single team by ``(department, name)`` or ``None`` if absent.

        Returns:
            The department-tagged team view, or ``None`` when the department
            or the team does not exist.
        """
        depts = await read_company_departments(self._app_state)
        try:
            _, dept = find_department(depts, department)
            _, team = find_team(teams_of(dept), team_name)
        except NotFoundError:
            return None
        return _team_view(department=persisted_name(dept, "Department"), team=team)

    async def create_team(
        self,
        *,
        department: NotBlankStr,
        name: NotBlankStr,
        lead: NotBlankStr,
        actor_id: NotBlankStr,
        members: Sequence[str] = (),
    ) -> dict[str, object]:
        """Create a team within a department, auditing the event.

        Returns:
            The newly created department-tagged team view.

        Raises:
            NotFoundError: If the department does not exist.
            ConflictError: If a team with this name already exists there.
            ValidationError: If the team data is invalid.
        """
        team_dict: dict[str, object] = {
            "name": name,
            "lead": lead,
            "members": list(members),
        }
        async with ORG_SETTINGS_WRITE_LOCK:
            depts = await read_company_departments(self._app_state)
            dept_idx, dept = find_department(depts, department)
            teams = teams_of(dept)
            check_team_name_unique(teams, name)
            validate_team_model(team_dict)
            teams.append(team_dict)
            depts[dept_idx] = {**dept, "teams": teams}
            await persist_company_departments(self._app_state, depts)
            dept_name = persisted_name(dept, "Department")
        logger.info(
            TEAM_CREATED_VIA_MCP,
            department=dept_name,
            team=name,
            actor_id=actor_id,
        )
        return _team_view(department=dept_name, team=team_dict)

    async def update_team(  # noqa: PLR0913 -- one kwarg per patchable team field
        self,
        *,
        department: NotBlankStr,
        team_name: NotBlankStr,
        actor_id: NotBlankStr,
        name: NotBlankStr | None = None,
        lead: NotBlankStr | None = None,
        members: Sequence[str] | None = None,
    ) -> dict[str, object] | None:
        """Patch a team (rename / change lead / replace members).

        Only provided fields change. Returns ``None`` when the department or
        team is absent so the handler maps onto ``not_found``.

        Returns:
            The updated department-tagged team view, or ``None``.

        Raises:
            ConflictError: If a rename collides with an existing team name.
            ValidationError: If the updated team data is invalid.
        """
        async with ORG_SETTINGS_WRITE_LOCK:
            depts = await read_company_departments(self._app_state)
            try:
                dept_idx, dept = find_department(depts, department)
                teams = teams_of(dept)
                team_idx, team = find_team(teams, team_name)
            except NotFoundError:
                return None
            updated = {**team}
            if name is not None:
                check_team_name_unique(teams, name, exclude_index=team_idx)
                updated["name"] = name
            if lead is not None:
                updated["lead"] = lead
            if members is not None:
                updated["members"] = list(members)
            validate_team_model(updated)
            teams[team_idx] = updated
            depts[dept_idx] = {**dept, "teams": teams}
            await persist_company_departments(self._app_state, depts)
            dept_name = persisted_name(dept, "Department")
        logger.info(
            TEAM_UPDATED_VIA_MCP,
            department=dept_name,
            team=updated["name"],
            actor_id=actor_id,
        )
        return _team_view(department=dept_name, team=updated)

    async def delete_team(
        self,
        *,
        department: NotBlankStr,
        team_name: NotBlankStr,
        actor_id: NotBlankStr,
        reason: NotBlankStr,
    ) -> bool:
        """Remove a team, auditing only on an actual removal.

        Returns:
            ``True`` when a team was removed, ``False`` when the department
            or team does not exist.
        """
        async with ORG_SETTINGS_WRITE_LOCK:
            depts = await read_company_departments(self._app_state)
            try:
                dept_idx, dept = find_department(depts, department)
                teams = teams_of(dept)
                team_idx, _ = find_team(teams, team_name)
            except NotFoundError:
                return False
            teams.pop(team_idx)
            depts[dept_idx] = {**dept, "teams": teams}
            await persist_company_departments(self._app_state, depts)
            dept_name = persisted_name(dept, "Department")
        logger.info(
            TEAM_DELETED_VIA_MCP,
            department=dept_name,
            team=team_name,
            actor_id=actor_id,
            reason=reason,
        )
        return True
