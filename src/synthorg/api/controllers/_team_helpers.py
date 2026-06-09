"""Persisted department/team navigation and validation helpers.

Pure functions over the raw ``company.departments`` JSON structure used
by ``TeamController``: locating departments and teams, narrowing the
loosely-typed persisted JSON, enforcing name uniqueness, validating team
dicts against the ``Team`` model, and writing the full list back to
settings. Kept separate from the controller so the request-handling
surface stays focused on routing.
"""

import json

from synthorg._core.features import require_service
from synthorg.api.state import AppState
from synthorg.core.company import Team
from synthorg.core.domain_errors import ConflictError, NotFoundError, ValidationError
from synthorg.core.normalization import normalize_identifier
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_RESOURCE_CONFLICT,
    API_RESOURCE_NOT_FOUND,
    API_VALIDATION_FAILED,
)
from synthorg.settings.state import SettingsStateSlice

logger = get_logger(__name__)


def _persisted_name(record: dict[str, object], record_type: str) -> str:
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
    depts: list[dict[str, object]],
    name: str,
) -> tuple[int, dict[str, object]]:
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


def _teams_of(dept: dict[str, object]) -> list[dict[str, object]]:
    """Return the department's ``teams`` as a fresh list of dicts.

    Narrows the persisted JSON value: a missing or malformed ``teams``
    entry yields an empty list, and non-dict items are dropped.

    Returns:
        Mutable list of team dicts.
    """
    raw = dept.get("teams", [])
    if not isinstance(raw, list):
        return []
    return [team for team in raw if isinstance(team, dict)]


def _member_list(record: dict[str, object]) -> list[object]:
    """Return the record's ``members`` as a fresh list, narrowing JSON.

    Returns:
        Mutable list of member entries (empty when absent/malformed).
    """
    raw = record.get("members", [])
    return list(raw) if isinstance(raw, list) else []


def _find_team(
    teams: list[dict[str, object]],
    team_name: str,
) -> tuple[int, dict[str, object]]:
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
    teams: list[dict[str, object]],
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


def _validate_team_model(team_dict: dict[str, object]) -> Team:
    """Validate a team dict by constructing a Team model.

    Args:
        team_dict: Raw team dict.

    Returns:
        Validated Team instance.

    Raises:
        ValidationError: If validation fails.
    """
    try:
        return Team.model_validate(team_dict)
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


async def _persist_departments(
    app_state: AppState,
    depts: list[dict[str, object]],
) -> None:
    """Write the full departments list back to settings."""
    await require_service(
        app_state.slice(SettingsStateSlice).settings_service, "Settings Service"
    ).set(
        "company",
        "departments",
        json.dumps(depts),
    )
