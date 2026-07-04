# module-kind: code
"""Company department/team navigation + settings read/write (shared).

Pure navigation + validation over the raw ``company.departments`` JSON
structure, plus the company-departments settings read/write. Lives in the
organization layer (no controller imports) so both the REST ``TeamController``
and the MCP ``TeamService`` share one implementation and, via
:data:`~synthorg.organization.settings_write_lock.ORG_SETTINGS_WRITE_LOCK`,
one write lock. Teams are sub-documents of ``company.departments[*].teams``;
there is no separate durable team store.
"""

import json
from typing import TYPE_CHECKING

from synthorg._core.features import require_service
from synthorg.core.company_departments import Team
from synthorg.core.domain_errors import ConflictError, DomainError, NotFoundError
from synthorg.core.domain_errors import ValidationError as DomainValidationError
from synthorg.core.normalization import normalize_identifier
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_RESOURCE_CONFLICT,
    API_RESOURCE_NOT_FOUND,
    API_VALIDATION_FAILED,
)
from synthorg.settings.errors import SettingNotFoundError
from synthorg.settings.state import SettingsStateSlice

if TYPE_CHECKING:
    from synthorg.api.state_slices import AppStateSliceMixin

logger = get_logger(__name__)

_DEPARTMENTS_KEY = "departments"


def persisted_name(record: dict[str, object], record_type: str) -> str:
    """Read the ``name`` field from a persisted record, asserting it is a str.

    Returns:
        The ``name`` field as a ``str``.

    Raises:
        ValidationError: If ``name`` is missing or not a string (corrupt data).
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
        raise DomainValidationError(msg)
    return value


def find_department(
    depts: list[dict[str, object]],
    name: str,
) -> tuple[int, dict[str, object]]:
    """Find a department by name (case-insensitive).

    Returns:
        Tuple of (index, department dict).

    Raises:
        NotFoundError: If not found.
        ValidationError: If a persisted record has a non-string name.
    """
    target = normalize_identifier(name)
    for idx, dept in enumerate(depts):
        if normalize_identifier(persisted_name(dept, "Department")) == target:
            return idx, dept
    logger.warning(API_RESOURCE_NOT_FOUND, resource="department", name=name)
    msg = f"Department {name!r} not found"
    raise NotFoundError(msg)


def teams_of(dept: dict[str, object]) -> list[dict[str, object]]:
    """Return the department's ``teams`` as a fresh list of dicts.

    Returns:
        Mutable list of team dicts (empty when absent/malformed).
    """
    raw = dept.get("teams", [])
    if not isinstance(raw, list):
        return []
    return [team for team in raw if isinstance(team, dict)]


def member_list(record: dict[str, object]) -> list[object]:
    """Return the record's ``members`` as a fresh list, narrowing JSON.

    Returns:
        Mutable list of member entries (empty when absent/malformed).
    """
    raw = record.get("members", [])
    return list(raw) if isinstance(raw, list) else []


def find_team(
    teams: list[dict[str, object]],
    team_name: str,
) -> tuple[int, dict[str, object]]:
    """Find a team by name within a department's teams list.

    Returns:
        Tuple of (index, team dict).

    Raises:
        NotFoundError: If not found.
        ValidationError: If a persisted record has a non-string name.
    """
    target = normalize_identifier(team_name)
    for idx, team in enumerate(teams):
        if normalize_identifier(persisted_name(team, "Team")) == target:
            return idx, team
    logger.warning(API_RESOURCE_NOT_FOUND, resource="team", name=team_name)
    msg = f"Team {team_name!r} not found"
    raise NotFoundError(msg)


def check_team_name_unique(
    teams: list[dict[str, object]],
    name: str,
    *,
    exclude_index: int | None = None,
) -> None:
    """Raise ConflictError if a team with this name already exists.

    Raises:
        ConflictError: If a name collision is detected.
        ValidationError: If a persisted record has a non-string name.
    """
    target = normalize_identifier(name)
    for idx, team in enumerate(teams):
        if idx == exclude_index:
            continue
        if normalize_identifier(persisted_name(team, "Team")) == target:
            logger.warning(
                API_RESOURCE_CONFLICT,
                resource="team",
                name=name,
                reason="duplicate_team_name",
            )
            msg = f"Team {name!r} already exists in this department"
            raise ConflictError(msg)


def validate_team_model(team_dict: dict[str, object]) -> Team:
    """Validate a team dict by constructing a :class:`Team` model.

    Returns:
        Validated :class:`Team` instance.

    Raises:
        ValidationError: If validation fails.
    """
    try:
        return Team.model_validate(team_dict)
    except (ValueError, TypeError) as exc:
        logger.warning(
            API_VALIDATION_FAILED,
            reason="team_model_validation_failed",
            team_name=team_dict.get("name"),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Team validation failed: {safe_error_description(exc)}"
        raise DomainValidationError(msg) from exc


async def read_company_departments(
    app_state: AppStateSliceMixin,
) -> list[dict[str, object]]:
    """Read the raw ``company.departments`` list from settings.

    Returns:
        The parsed departments list, or ``[]`` when the setting is missing
        or empty.

    Raises:
        DomainError: If the stored JSON is corrupt (invalid JSON or not a
            list of objects).
    """
    settings_svc = require_service(
        app_state.slice(SettingsStateSlice).settings_service, "Settings Service"
    )
    try:
        entry = await settings_svc.get("company", _DEPARTMENTS_KEY)
    except SettingNotFoundError:
        return []
    if not entry.value:
        return []
    try:
        parsed = json.loads(entry.value)
    except json.JSONDecodeError as exc:
        logger.warning(
            API_VALIDATION_FAILED,
            key=_DEPARTMENTS_KEY,
            action="corrupt_setting_json",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = "Setting 'company/departments' contains invalid JSON"
        raise DomainError(msg) from exc
    if not isinstance(parsed, list) or not all(
        isinstance(item, dict) for item in parsed
    ):
        logger.error(
            API_VALIDATION_FAILED,
            key=_DEPARTMENTS_KEY,
            action="corrupt_setting_type",
            expected="list[dict]",
            got=type(parsed).__name__,
        )
        msg = "Setting 'company/departments' is not a list of objects"
        raise DomainError(msg)
    return parsed


async def persist_company_departments(
    app_state: AppStateSliceMixin,
    depts: list[dict[str, object]],
) -> None:
    """Write the full ``company.departments`` list back to settings."""
    await require_service(
        app_state.slice(SettingsStateSlice).settings_service, "Settings Service"
    ).set("company", _DEPARTMENTS_KEY, json.dumps(depts))


__all__ = [
    "check_team_name_unique",
    "find_department",
    "find_team",
    "member_list",
    "persist_company_departments",
    "persisted_name",
    "read_company_departments",
    "teams_of",
    "validate_team_model",
]
