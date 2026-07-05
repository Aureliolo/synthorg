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
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Final

from synthorg._core.features import require_service
from synthorg.core.company_departments import Team
from synthorg.core.concurrency.cas_retry import CASRetryHandler
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ConflictError, DomainError, NotFoundError
from synthorg.core.domain_errors import ValidationError as DomainValidationError
from synthorg.core.normalization import normalize_identifier
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_REQUEST_ERROR,
    API_RESOURCE_CONFLICT,
    API_RESOURCE_NOT_FOUND,
    API_VALIDATION_FAILED,
)
from synthorg.organization.settings_write_lock import ORG_SETTINGS_WRITE_LOCK
from synthorg.settings.errors import SettingNotFoundError
from synthorg.settings.state import SettingsStateSlice, config_resolver_of

if TYPE_CHECKING:
    from synthorg.api.state_slices import AppStateSliceMixin

logger = get_logger(__name__)

_DEPARTMENTS_KEY = "departments"

#: Fallback CAS retry budget when the settings resolver is unavailable; equals
#: the registered ``coordination.company_departments_cas_retry_attempts``
#: default so a transient settings outage never collapses the budget to zero.
#: An ``asyncio.Lock`` serialises in-process writers, but the API can run
#: multiple worker processes (``api/server.py`` supports ``workers>1``), so
#: cross-process safety rests on CAS, not the lock.
#: See docs/reference/retry-patterns.md: Pattern C/CAS.
_COMPANY_DEPARTMENTS_CAS_FALLBACK_ATTEMPTS: Final[int] = 3


async def _resolve_company_departments_cas_attempts(
    app_state: AppStateSliceMixin,
) -> int:
    """Resolve the ``company.departments`` CAS retry budget via settings.

    Falls back to :data:`_COMPANY_DEPARTMENTS_CAS_FALLBACK_ATTEMPTS` when no
    resolver is wired or the lookup fails, so a transient settings outage
    cannot collapse the retry budget to zero.

    Returns:
        The resolved maximum CAS attempts.
    """
    if app_state.slice(SettingsStateSlice).config_resolver is None:
        return _COMPANY_DEPARTMENTS_CAS_FALLBACK_ATTEMPTS
    try:
        return await config_resolver_of(app_state).get_int(
            "coordination",
            "company_departments_cas_retry_attempts",
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="organization.company_departments.cas_retry_resolve",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback=_COMPANY_DEPARTMENTS_CAS_FALLBACK_ATTEMPTS,
        )
        return _COMPANY_DEPARTMENTS_CAS_FALLBACK_ATTEMPTS


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


def _safe_persisted_name(
    record: dict[str, object], record_type: str, index: int
) -> str | None:
    """Read a record's ``name`` for comparison, tolerating corruption.

    Unlike :func:`persisted_name`, a non-string name logs a warning and
    returns ``None`` instead of raising, so a single corrupt/legacy
    record (e.g. one missing its ``name``) cannot break a lookup or a
    listing for every *other*, unrelated record in the same blob.

    Args:
        record: The persisted record dict.
        record_type: ``"Department"`` / ``"Team"`` for the log payload.
        index: The record's position in the persisted blob, logged so an
            operator can pinpoint which sibling is corrupt.

    Returns:
        The ``name`` as a ``str``, or ``None`` when it is absent / not a
        string (the record is then skipped by the caller).
    """
    value = record.get("name")
    if isinstance(value, str):
        return value
    logger.warning(
        API_VALIDATION_FAILED,
        record_type=record_type,
        record_index=index,
        reason="non_string_persisted_name_skipped",
        value_type=type(value).__name__,
    )
    return None


def find_department(
    depts: list[dict[str, object]],
    name: str,
) -> tuple[int, dict[str, object]]:
    """Find a department by name (case-insensitive).

    A department record with a missing / non-string ``name`` is skipped
    (logged, not raised), so one corrupt record never blocks lookups for
    the others.

    Returns:
        Tuple of (index, department dict).

    Raises:
        NotFoundError: If not found.
    """
    target = normalize_identifier(name)
    for idx, dept in enumerate(depts):
        dept_name = _safe_persisted_name(dept, "Department", idx)
        if dept_name is not None and normalize_identifier(dept_name) == target:
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

    A team record with a missing / non-string ``name`` is skipped
    (logged, not raised), so one corrupt record never blocks lookups for
    the others.

    Returns:
        Tuple of (index, team dict).

    Raises:
        NotFoundError: If not found.
    """
    target = normalize_identifier(team_name)
    for idx, team in enumerate(teams):
        team_name_value = _safe_persisted_name(team, "Team", idx)
        if team_name_value is not None and normalize_identifier(team_name_value) == (
            target
        ):
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

    A corrupt sibling (missing / non-string ``name``) is skipped rather
    than raised: it cannot collide with *name*, so it must not block an
    otherwise-valid create/rename.

    Raises:
        ConflictError: If a name collision is detected.
    """
    target = normalize_identifier(name)
    for idx, team in enumerate(teams):
        if idx == exclude_index:
            continue
        existing = _safe_persisted_name(team, "Team", idx)
        if existing is not None and normalize_identifier(existing) == target:
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


def _parse_departments_json(value: str) -> list[dict[str, object]]:
    """Parse a stored ``company.departments`` JSON string into dicts.

    Returns:
        The parsed list, or ``[]`` when *value* is empty.

    Raises:
        DomainError: If the JSON is invalid or not a list of objects.
    """
    if not value:
        return []
    try:
        parsed = json.loads(value)
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
    return _parse_departments_json(entry.value)


async def read_company_departments_versioned(
    app_state: AppStateSliceMixin,
) -> tuple[list[dict[str, object]], str]:
    """Read ``company.departments`` plus its compare-and-set version token.

    Reads DB state directly (bypassing the cache/fallback chain) so the
    version token is authoritative for a subsequent CAS write.

    Returns:
        A ``(departments, version)`` pair. ``version`` is the setting's
        ``updated_at`` token, ``""`` for a never-written key (the
        first-write sentinel), threaded back into
        :func:`persist_company_departments` for compare-and-set.

    Raises:
        DomainError: If the stored JSON is corrupt.
    """
    settings_svc = require_service(
        app_state.slice(SettingsStateSlice).settings_service, "Settings Service"
    )
    value, version = await settings_svc.get_versioned("company", _DEPARTMENTS_KEY)
    return _parse_departments_json(value), version


async def persist_company_departments(
    app_state: AppStateSliceMixin,
    depts: list[dict[str, object]],
    *,
    expected_updated_at: str | None = None,
) -> None:
    """Write the full ``company.departments`` list back to settings.

    When *expected_updated_at* is supplied the write is compare-and-set:
    it raises :class:`~synthorg.core.domain_errors.VersionConflictError`
    if the stored version moved since the read, so a concurrent writer's
    update is never silently overwritten.
    """
    await require_service(
        app_state.slice(SettingsStateSlice).settings_service, "Settings Service"
    ).set(
        "company",
        _DEPARTMENTS_KEY,
        json.dumps(depts),
        expected_updated_at=expected_updated_at,
    )


async def with_company_departments_cas[T](
    app_state: AppStateSliceMixin,
    read: Callable[[], Awaitable[tuple[T, str]]],
    write: Callable[[T, str], Awaitable[None]],
) -> T:
    """Run an arbitrary CAS read-modify-write over ``company.departments``.

    The shared seam for the team / setup / template-pack writers of the blob:
    *read* loads + validates + builds the new value (returning
    ``(new_value, version)``) and *write* persists it guarded by *version*,
    raising :class:`~synthorg.core.domain_errors.VersionConflictError` on a
    stale token so the handler retries. Both run under a per-call CAS handler
    and the in-process
    :data:`~synthorg.organization.settings_write_lock.ORG_SETTINGS_WRITE_LOCK`,
    so a writer touching ``company.departments`` alongside another key (e.g.
    template-pack apply, which also writes ``company.agents``) shares the
    exact retry policy, resource label, and write lock those callers use. The
    retry budget resolves per call through
    ``coordination.company_departments_cas_retry_attempts`` so an operator can
    tune a sustained-contention burst without restarting the process.

    REST department CRUD (the ``OrgMutationService`` in
    ``synthorg.api.services.org_mutations``) is a *separate* CAS writer of
    the same key with its own handler and no
    lock; it does not share this seam. Correctness across all writers rests
    only on the shared per-key CAS token (``expected_updated_at``) every path
    passes to the settings layer, not on this lock, which is an in-process
    optimisation for the callers that use it.

    Returns:
        The winning *read*'s ``new_value`` once *write* succeeds.
    """
    max_attempts = await _resolve_company_departments_cas_attempts(app_state)
    handler = CASRetryHandler(resource="company_departments", max_attempts=max_attempts)
    async with ORG_SETTINGS_WRITE_LOCK:
        return await handler.execute(read, write)


async def mutate_company_departments[T](
    app_state: AppStateSliceMixin,
    mutate: Callable[[list[dict[str, object]]], T],
) -> T:
    """Run a compare-and-set read-modify-write over ``company.departments``.

    *mutate* receives the parsed departments list, mutates it in place
    (raising ``NotFoundError`` / ``ConflictError`` / ``ValidationError``
    to abort without retry) and returns a caller result. The list is
    persisted through :func:`with_company_departments_cas`, retrying on a
    cross-writer version conflict, so a concurrent department / team / setup
    write can never silently lose this update.

    Returns:
        Whatever *mutate* returned on the winning attempt.
    """
    captured: dict[str, T] = {}

    async def read() -> tuple[list[dict[str, object]], str]:
        depts, version = await read_company_departments_versioned(app_state)
        captured["result"] = mutate(depts)
        return depts, version

    async def write(new_depts: list[dict[str, object]], version: str) -> None:
        await persist_company_departments(
            app_state, new_depts, expected_updated_at=version
        )

    await with_company_departments_cas(app_state, read, write)
    return captured["result"]


__all__ = [
    "check_team_name_unique",
    "find_department",
    "find_team",
    "member_list",
    "mutate_company_departments",
    "persist_company_departments",
    "persisted_name",
    "read_company_departments",
    "read_company_departments_versioned",
    "teams_of",
    "validate_team_model",
    "with_company_departments_cas",
]
