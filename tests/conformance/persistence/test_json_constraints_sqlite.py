"""SQLite ``CHECK (json_valid(...))`` constraint conformance tests.

The Postgres side stores these columns as ``JSONB`` which validates
implicitly; SQLite stores them as ``TEXT`` and the audit migration
``20260503181821_json_check_constraints.sql`` adds CHECK constraints
to bring the same shape guarantee.  This module asserts the integrity
error fires on bad input -- a parity floor between the two backends.

Postgres is skipped via the ``backend_name == "sqlite"`` guard so the
parametrised dual-backend fixture stays usable; the JSONB side is
already exercised by the existing repository conformance tests.
"""

from typing import cast

import aiosqlite
import pytest

from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration


class TestSqliteJsonValidConstraints:
    """``CHECK (json_valid(...))`` integrity coverage on SQLite TEXT columns."""

    async def test_provider_audit_payload_rejects_non_json(
        self,
        backend: PersistenceBackend,
    ) -> None:  # lint-allow: dual-backend-parity -- exercises SQLite CHECK json_valid()
        """``provider_audit_events.payload`` rejects malformed JSON."""
        if backend.backend_name != "sqlite":
            pytest.skip("SQLite-only constraint")
        conn = cast("aiosqlite.Connection", backend.get_db())

        async def _attempt_insert() -> None:
            await conn.execute(
                "INSERT INTO provider_audit_events "
                "(provider_name, event_type, actor_id, actor_label, "
                "payload, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "example-provider",
                    "credential.rotated",
                    "actor-1",
                    "Operator",
                    "{not-valid-json",
                    "2026-05-03T12:00:00+00:00",
                ),
            )

        with pytest.raises(aiosqlite.IntegrityError):
            await _attempt_insert()

    @pytest.mark.parametrize(
        "column_name",
        ["default_models", "supported_auth_types", "candidate_urls"],
    )
    async def test_preset_overrides_nullable_json_columns_reject_non_json(
        self,
        backend: PersistenceBackend,
        column_name: str,
    ) -> None:  # lint-allow: dual-backend-parity -- exercises SQLite CHECK json_valid()
        """Each nullable JSON override column rejects malformed JSON.

        The migration installs a separate ``CHECK (col IS NULL OR
        json_valid(col))`` clause per column. Parametrising across
        all three guards against a typo or missed column in the
        schema/migration that would otherwise pass green if only one
        column was exercised.
        """
        if backend.backend_name != "sqlite":
            pytest.skip("SQLite-only constraint")
        conn = cast("aiosqlite.Connection", backend.get_db())

        async def _attempt_insert() -> None:
            # ``column_name`` is a closed parametrise enum, never user
            # input, so the f-string is safe; suppress S608 to keep
            # parametrise readable rather than building per-column
            # branches.
            await conn.execute(
                "INSERT INTO preset_overrides "  # noqa: S608
                f"(preset_name, base_url, {column_name}, "
                "updated_at, updated_by) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    f"example-preset-{column_name}",
                    "https://example.invalid",
                    "not-json",
                    "2026-05-03T12:00:00+00:00",
                    "operator-1",
                ),
            )

        with pytest.raises(aiosqlite.IntegrityError):
            await _attempt_insert()

    async def test_preset_overrides_nullable_columns_accept_null(
        self,
        backend: PersistenceBackend,
    ) -> None:  # lint-allow: dual-backend-parity -- exercises SQLite CHECK json_valid()
        """The ``IS NULL OR json_valid()`` form admits NULL for the nullable
        JSON columns so existing rows that omit overrides keep working."""
        if backend.backend_name != "sqlite":
            pytest.skip("SQLite-only constraint")
        conn = cast("aiosqlite.Connection", backend.get_db())
        await conn.execute(
            "INSERT INTO preset_overrides "
            "(preset_name, base_url, default_models, "
            "supported_auth_types, candidate_urls, "
            "updated_at, updated_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "example-preset-nulls",
                "https://example.invalid",
                None,
                None,
                None,
                "2026-05-03T12:00:00+00:00",
                "operator-1",
            ),
        )
        await conn.commit()
        cur = await conn.execute(
            "SELECT preset_name FROM preset_overrides WHERE preset_name = ?",
            ("example-preset-nulls",),
        )
        row = await cur.fetchone()
        assert row is not None
