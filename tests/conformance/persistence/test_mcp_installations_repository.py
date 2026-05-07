"""Conformance tests for ``McpInstallationRepository``.

MCP catalog installation records have SQLite + Postgres
implementations; this file exercises both behind the shared
``backend`` fixture so the semantics stay in lock-step across
backends.
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.integrations.mcp_catalog.installations import McpInstallation
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration


def _installation(
    entry_id: str = "catalog_entry_1",
    connection_name: str | None = None,
    at: datetime | None = None,
) -> McpInstallation:
    """Build an MCP installation row.

    ``connection_name`` defaults to ``None`` (connectionless server)
    because the connections table has a FK into connection names and
    these conformance tests do not exercise the connections catalog.
    Pass a non-None name only if the caller has also inserted a
    matching connection row.
    """
    return McpInstallation(
        catalog_entry_id=NotBlankStr(entry_id),
        connection_name=(
            NotBlankStr(connection_name) if connection_name is not None else None
        ),
        installed_at=at or datetime.now(UTC),
    )


class TestMcpInstallationRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        inst = _installation()
        await backend.mcp_installations.save(inst)
        fetched = await backend.mcp_installations.get(
            NotBlankStr("catalog_entry_1"),
        )
        assert fetched is not None
        assert fetched.catalog_entry_id == "catalog_entry_1"
        # Default uses a connectionless server -- no FK dependency.
        assert fetched.connection_name is None

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert (
            await backend.mcp_installations.get(NotBlankStr("never_installed")) is None
        )

    async def test_save_is_idempotent_on_catalog_entry_id(
        self, backend: PersistenceBackend
    ) -> None:
        first = _installation(
            "catalog_entry_idempotent",
            at=datetime.now(UTC),
        )
        later = datetime.now(UTC) + timedelta(seconds=2)
        second = _installation(
            "catalog_entry_idempotent",
            at=later,
        )
        await backend.mcp_installations.save(first)
        await backend.mcp_installations.save(second)
        fetched = await backend.mcp_installations.get(
            NotBlankStr("catalog_entry_idempotent"),
        )
        assert fetched is not None
        # Upsert overwrites installed_at with the newer value.
        assert fetched.installed_at >= first.installed_at

    async def test_save_with_null_connection_name(
        self, backend: PersistenceBackend
    ) -> None:
        """Connectionless servers store ``connection_name=None``."""
        await backend.mcp_installations.save(
            _installation("catalog_connectionless", connection_name=None),
        )
        fetched = await backend.mcp_installations.get(
            NotBlankStr("catalog_connectionless"),
        )
        assert fetched is not None
        assert fetched.connection_name is None

    async def test_list_items(self, backend: PersistenceBackend) -> None:
        await backend.mcp_installations.save(_installation("cat_a"))
        await backend.mcp_installations.save(_installation("cat_b"))
        rows = await backend.mcp_installations.list_items()
        ids = {r.catalog_entry_id for r in rows}
        assert {"cat_a", "cat_b"} <= ids

    async def test_list_items_pagination(self, backend: PersistenceBackend) -> None:
        # Insert with monotonically increasing installed_at so the
        # deterministic ORDER BY installed_at, catalog_entry_id places
        # the rows in a known order.
        base = datetime.now(UTC)
        for i in range(5):
            await backend.mcp_installations.save(
                _installation(
                    f"cat_pag_{i}",
                    at=base + timedelta(seconds=i),
                ),
            )

        page_one = await backend.mcp_installations.list_items(limit=2, offset=0)
        page_two = await backend.mcp_installations.list_items(limit=2, offset=2)
        page_three = await backend.mcp_installations.list_items(limit=2, offset=4)

        assert len(page_one) == 2
        assert len(page_two) == 2
        assert len(page_three) == 1
        assert [r.catalog_entry_id for r in page_one] == ["cat_pag_0", "cat_pag_1"]
        assert [r.catalog_entry_id for r in page_two] == ["cat_pag_2", "cat_pag_3"]
        assert [r.catalog_entry_id for r in page_three] == ["cat_pag_4"]

    @pytest.mark.parametrize(
        ("limit", "offset"),
        [
            (0, 0),
            (-1, 0),
            (1, -1),
            (True, 0),
            (1, False),
        ],
    )
    async def test_list_items_rejects_invalid_pagination(
        self,
        backend: PersistenceBackend,
        limit: object,
        offset: object,
    ) -> None:
        # Lock the QueryError contract across every backend so a
        # silently-coercing impl can't drift away from the durable
        # ones (sqlite + postgres reject these via
        # ``validate_pagination_args``; the in-memory shim must too).
        with pytest.raises(QueryError):
            await backend.mcp_installations.list_items(
                limit=limit,  # type: ignore[arg-type]
                offset=offset,  # type: ignore[arg-type]
            )

    async def test_delete_returns_true_when_present(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.mcp_installations.save(_installation("cat_delete"))
        deleted = await backend.mcp_installations.delete(
            NotBlankStr("cat_delete"),
        )
        assert deleted is True
        assert await backend.mcp_installations.get(NotBlankStr("cat_delete")) is None

    async def test_delete_returns_false_when_missing(
        self, backend: PersistenceBackend
    ) -> None:
        deleted = await backend.mcp_installations.delete(
            NotBlankStr("never_existed"),
        )
        assert deleted is False
