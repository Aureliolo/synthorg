# module-kind: repository
"""Postgres repository for the durable role registry.

Sibling of :class:`SQLiteRoleRegistryRepository` backed by
``psycopg_pool.AsyncConnectionPool``. Id-keyed CRUD keyed by ``role.name``;
``save`` upserts on the primary key. The role's tuple fields
(``required_skills`` / ``tool_access``) are stored in JSONB columns.
"""

import json
from typing import NoReturn

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

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

logger = get_logger(__name__)

_SELECT_COLS = (
    "name, department, required_skills, authority_level, tool_access, "
    "system_prompt_template, description, is_builtin, created_at, updated_at"
)


def _str_tuple(raw: object) -> tuple[NotBlankStr, ...]:
    """Decode a JSONB array (list or JSON string) into non-blank strings.

    Returns:
        The decoded tuple.

    Raises:
        TypeError: When the decoded value is not a JSON array.
        ValueError: When any element is not a non-blank string. Both surface
            as ``QueryError`` via ``_row_to_record`` so corrupt persisted
            payloads fail loudly instead of being coerced into a wrong tuple.
    """
    decoded = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(decoded, list):
        msg = f"Expected a JSON array, got {type(decoded).__name__}"
        raise TypeError(msg)
    if not all(isinstance(item, str) and item.strip() for item in decoded):
        msg = "Expected a JSON array of non-blank strings"
        raise ValueError(msg)
    return tuple(NotBlankStr(item) for item in decoded)


def _row_to_record(row: DictRow) -> RoleRecord:
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
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        logger.warning(
            ROLE_REGISTRY_PERSISTENCE_FAILED,
            operation="deserialize",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Failed to parse role row: {type(exc).__name__}"
        raise QueryError(msg) from exc


class PostgresRoleRegistryRepository:
    """Postgres-backed durable role registry.

    Args:
        pool: Async connection pool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

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
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (name) DO UPDATE SET
                department = EXCLUDED.department,
                required_skills = EXCLUDED.required_skills,
                authority_level = EXCLUDED.authority_level,
                tool_access = EXCLUDED.tool_access,
                system_prompt_template = EXCLUDED.system_prompt_template,
                description = EXCLUDED.description,
                is_builtin = EXCLUDED.is_builtin,
                updated_at = EXCLUDED.updated_at
        """
        role = entity.role
        params = (
            role.name,
            role.department.value,
            Jsonb(list(role.required_skills)),
            role.authority_level.value,
            Jsonb(list(role.tool_access)),
            role.system_prompt_template,
            role.description,
            entity.is_builtin,
            format_iso_utc(entity.created_at),
            format_iso_utc(entity.updated_at),
        )
        try:
            async with self._pool.connection() as conn:
                await conn.execute(sql, params)
                await conn.commit()
        except psycopg.Error as exc:
            self._raise_query_error("save role", exc)

    async def get(self, entity_id: NotBlankStr) -> RoleRecord | None:
        """Get the role for ``name``, or ``None``.

        Returns:
            The matching role record, or ``None``.

        Raises:
            QueryError: If the database query fails.
        """
        sql = f"SELECT {_SELECT_COLS} FROM roles WHERE name = %s"  # noqa: S608
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (entity_id,))
                row = await cur.fetchone()
        except psycopg.Error as exc:
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
            "ORDER BY name ASC LIMIT %s OFFSET %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (effective_limit, offset))
                rows = await cur.fetchall()
            return tuple(_row_to_record(r) for r in rows)
        except psycopg.Error as exc:
            self._raise_query_error("list roles", exc)

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete the role for ``name``. ``True`` iff present.

        Returns:
            ``True`` when a row was removed, ``False`` otherwise.

        Raises:
            QueryError: If the database query fails.
        """
        sql = "DELETE FROM roles WHERE name = %s"
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, (entity_id,))
                rowcount = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            self._raise_query_error("delete role", exc)
        return rowcount > 0

    def _raise_query_error(self, operation: str, exc: Exception) -> NoReturn:
        logger.warning(
            ROLE_REGISTRY_PERSISTENCE_FAILED,
            operation=operation,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Failed to {operation}: {type(exc).__name__}"
        raise QueryError(msg) from exc


__all__ = ["PostgresRoleRegistryRepository"]
