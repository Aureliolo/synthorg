# module-kind: repository
"""SQLite repository for the durable role registry.

Id-keyed CRUD keyed by ``role.name``. ``save`` upserts on the ``name`` primary
key (the boot seed upserts each built-in once). The role's tuple fields
(``required_skills`` / ``tool_access``) are stored as JSON arrays; the
``department`` and ``authority_level`` enums are stored as their string values.
"""

import json
from typing import NoReturn

import aiosqlite

from synthorg.core.persistence_errors import QueryError
from synthorg.core.role import Role
from synthorg.core.role_record import RoleRecord
from synthorg.core.types import NotBlankStr
from synthorg.hr.seniority import SeniorityLevel
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.role import ROLE_REGISTRY_PERSISTENCE_FAILED
from synthorg.organization.enums import DepartmentName
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    coerce_row_timestamp,
    format_iso_utc,
    validate_pagination_args,
)
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

_SELECT_COLS = (
    "name, department, required_skills, authority_level, tool_access, "
    "system_prompt_template, description, is_builtin, created_at, updated_at"
)


def _str_tuple(raw: object) -> tuple[NotBlankStr, ...]:
    """Decode a JSON string array into a tuple of non-blank strings.

    Returns:
        The decoded tuple.
    """
    decoded = json.loads(str(raw))
    return tuple(NotBlankStr(str(item)) for item in decoded)


def _row_to_record(row: aiosqlite.Row) -> RoleRecord:
    """Convert a database row into a :class:`RoleRecord`.

    Returns:
        The reconstructed role record.

    Raises:
        QueryError: If the row contains corrupt or unparseable data.
    """
    try:
        template = row["system_prompt_template"]
        role = Role(
            name=NotBlankStr(str(row["name"])),
            department=DepartmentName(str(row["department"])),
            required_skills=_str_tuple(row["required_skills"]),
            authority_level=SeniorityLevel(str(row["authority_level"])),
            tool_access=_str_tuple(row["tool_access"]),
            system_prompt_template=(
                NotBlankStr(str(template)) if template is not None else None
            ),
            description=str(row["description"]),
        )
        return RoleRecord(
            role=role,
            is_builtin=bool(row["is_builtin"]),
            created_at=coerce_row_timestamp(row["created_at"]),
            updated_at=coerce_row_timestamp(row["updated_at"]),
        )
    except (ValueError, TypeError, KeyError, IndexError, json.JSONDecodeError) as exc:
        logger.warning(
            ROLE_REGISTRY_PERSISTENCE_FAILED,
            operation="deserialize",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Failed to parse role row: {type(exc).__name__}"
        raise QueryError(msg) from exc


class SQLiteRoleRegistryRepository:
    """SQLite-backed durable role registry.

    Args:
        db: An open aiosqlite connection.
        write_context: Async write-serialising context manager.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._db.row_factory = aiosqlite.Row
        self._write_context = write_context

    async def save(self, entity: RoleRecord) -> None:
        """Upsert a role keyed by ``role.name``.

        Raises:
            QueryError: On database errors.
        """
        sql = """
            INSERT INTO roles (
                name, department, required_skills, authority_level,
                tool_access, system_prompt_template, description, is_builtin,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                department = excluded.department,
                required_skills = excluded.required_skills,
                authority_level = excluded.authority_level,
                tool_access = excluded.tool_access,
                system_prompt_template = excluded.system_prompt_template,
                description = excluded.description,
                is_builtin = excluded.is_builtin,
                updated_at = excluded.updated_at
        """
        role = entity.role
        params = (
            role.name,
            role.department.value,
            json.dumps(list(role.required_skills), separators=(",", ":")),
            role.authority_level.value,
            json.dumps(list(role.tool_access), separators=(",", ":")),
            role.system_prompt_template,
            role.description,
            1 if entity.is_builtin else 0,
            format_iso_utc(entity.created_at),
            format_iso_utc(entity.updated_at),
        )
        async with self._write_context():
            try:
                await self._db.execute(sql, params)
                await self._db.commit()
            except (aiosqlite.Error, ValueError) as exc:
                await self._rollback("save")
                self._raise_query_error("save role", exc)

    async def get(self, entity_id: NotBlankStr) -> RoleRecord | None:
        """Get the role for ``name``, or ``None``.

        Returns:
            The matching role record, or ``None``.

        Raises:
            QueryError: If the database query fails.
        """
        sql = f"SELECT {_SELECT_COLS} FROM roles WHERE name = ?"  # noqa: S608
        try:
            async with self._db.execute(sql, (entity_id,)) as cursor:
                row = await cursor.fetchone()
        except (aiosqlite.Error, ValueError) as exc:
            self._raise_query_error("get role", exc)
        return None if row is None else _row_to_record(row)

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[RoleRecord, ...]:
        """List roles alphabetically by ``name`` (paginated).

        Returns:
            The roles, alphabetically by name.

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=ROLE_REGISTRY_PERSISTENCE_FAILED
        )
        sql = (
            f"SELECT {_SELECT_COLS} FROM roles "  # noqa: S608
            "ORDER BY name ASC LIMIT ? OFFSET ?"
        )
        try:
            async with self._db.execute(sql, (effective_limit, offset)) as cursor:
                rows = await cursor.fetchall()
            return tuple(_row_to_record(r) for r in rows)
        except QueryError:
            raise
        except (aiosqlite.Error, ValueError) as exc:
            self._raise_query_error("list roles", exc)

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete the role for ``name``. ``True`` iff present.

        Returns:
            ``True`` when a row was removed, ``False`` otherwise.

        Raises:
            QueryError: If the database query fails.
        """
        sql = "DELETE FROM roles WHERE name = ?"
        async with self._write_context():
            try:
                async with self._db.execute(sql, (entity_id,)) as cursor:
                    await self._db.commit()
                    return cursor.rowcount > 0
            except (aiosqlite.Error, ValueError) as exc:
                await self._rollback("delete")
                self._raise_query_error("delete role", exc)

    async def _rollback(self, operation: str) -> None:
        try:
            await self._db.rollback()
        except aiosqlite.Error as exc:
            logger.warning(
                ROLE_REGISTRY_PERSISTENCE_FAILED,
                operation=operation,
                phase="rollback",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    def _raise_query_error(self, operation: str, exc: Exception) -> NoReturn:
        logger.warning(
            ROLE_REGISTRY_PERSISTENCE_FAILED,
            operation=operation,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Failed to {operation}: {type(exc).__name__}"
        raise QueryError(msg) from exc


__all__ = ["SQLiteRoleRegistryRepository"]
