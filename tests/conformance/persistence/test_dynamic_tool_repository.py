"""Conformance tests for ``DynamicToolRepository`` (SQLite + Postgres).

The repository is not exposed on ``PersistenceBackend`` (the toolsmith's
``DynamicToolRegistry`` wires it directly), so this file builds the
backend-specific concrete repo over the migrated ``backend.get_db()``
handle. Both arms exercise the same protocol surface so SQLite and
Postgres divergence (TEXT vs JSONB ``parameters_schema`` / ``validation``,
INTEGER 0/1 vs BOOLEAN ``requires_network``, TEXT vs TIMESTAMPTZ
timestamps) is caught by the same assertion set.
"""

from datetime import UTC, datetime, timedelta
from typing import cast

import aiosqlite
import pytest

from synthorg.core.types import NotBlankStr
from synthorg.meta.toolsmith.models import (
    ToolBlueprint,
    ToolBlueprintState,
    ToolSandboxBackend,
    ToolValidationResult,
)
from synthorg.persistence.postgres.tool_blueprint_repo import (
    PostgresDynamicToolRepository,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.sqlite.tool_blueprint_repo import (
    SQLiteDynamicToolRepository,
)
from synthorg.persistence.tool_blueprint_protocol import (
    DynamicToolRepository,
    ToolBlueprintFilterSpec,
)

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
}


def _repo(backend: PersistenceBackend) -> DynamicToolRepository:
    """Return a concrete ``DynamicToolRepository`` bound to *backend*."""
    name = backend.backend_name
    handle = backend.get_db()
    if name == "sqlite":
        return SQLiteDynamicToolRepository(
            cast("aiosqlite.Connection", handle),
            write_context=backend.write_context,
        )
    if name == "postgres":
        from psycopg_pool import AsyncConnectionPool

        return PostgresDynamicToolRepository(cast("AsyncConnectionPool", handle))
    msg = f"Unknown backend: {name}"
    raise ValueError(msg)


def _blueprint(
    *,
    blueprint_id: str = "bp-001",
    name: str = "synthorg_textkit_slugify",
    capability: str | None = None,
    state: ToolBlueprintState = ToolBlueprintState.PENDING,
    sandbox_backend: ToolSandboxBackend = ToolSandboxBackend.DOCKER,
) -> ToolBlueprint:
    """Build a ``ToolBlueprint`` with sensible defaults.

    When ``capability`` is omitted, it is derived from ``name`` (stripping
    the ``synthorg_`` prefix and replacing the inner ``_`` with ``:``) so
    the model invariant ``name <=> capability`` is satisfied by default.
    Callers that want to test name/capability mismatch pass an explicit
    capability that disagrees with the name.
    """
    if capability is None:
        # ``name`` always starts with ``synthorg_`` and the regex enforces
        # one underscore between domain and action, so a 2-split that
        # drops the prefix recovers the matching capability tag.
        parts = name.split("_", 2)
        capability = f"{parts[1]}:{parts[2]}"
    validated_at = None
    activated_at = None
    retired_at = None
    validation = None
    # Every post-PENDING state carries the gate's validation record, so
    # the audit trail survives the lifecycle (the runtime model rejects a
    # validated/active/retired row without it).
    if state in {
        ToolBlueprintState.VALIDATED,
        ToolBlueprintState.ACTIVE,
        ToolBlueprintState.RETIRED,
    }:
        validated_at = _NOW + timedelta(minutes=1)
        validation = ToolValidationResult(
            passed=True,
            brief_passed=True,
            brief_score=88,
            baseline_score=100,
            candidate_score=101,
            margin=1,
            detail="passed",
        )
    if state is ToolBlueprintState.ACTIVE:
        activated_at = _NOW + timedelta(minutes=2)
    if state is ToolBlueprintState.RETIRED:
        activated_at = _NOW + timedelta(minutes=2)
        retired_at = _NOW + timedelta(minutes=3)
    return ToolBlueprint(
        id=blueprint_id,
        name=name,
        description="Slugify text deterministically.",
        capability=capability,
        parameters_schema=_SCHEMA,
        script_body="print('ok')",
        sandbox_backend=sandbox_backend,
        action_type="code:read",
        state=state,
        created_at=_NOW,
        validated_at=validated_at,
        activated_at=activated_at,
        retired_at=retired_at,
        validation=validation,
    )


class TestDynamicToolRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        bp = _blueprint()
        await repo.save(bp)

        fetched = await repo.get(bp.id)
        assert fetched is not None
        assert fetched.id == bp.id
        assert fetched.name == bp.name
        assert fetched.parameters_schema == _SCHEMA
        assert fetched.sandbox_backend is ToolSandboxBackend.DOCKER
        assert fetched.requires_network is False
        assert fetched.state is ToolBlueprintState.PENDING
        assert fetched.created_at.tzinfo is not None

    async def test_get_returns_none_when_absent(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        assert await repo.get("bp-missing") is None

    async def test_save_commits_visible_to_fresh_repo(
        self, backend: PersistenceBackend
    ) -> None:
        first = _repo(backend)
        bp = _blueprint(blueprint_id="bp-commit", name="synthorg_textkit_commit")
        await first.save(bp)

        second = _repo(backend)
        fetched = await second.get(bp.id)
        assert fetched is not None

    async def test_upsert_overwrites(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        bp = _blueprint(blueprint_id="bp-upsert", name="synthorg_textkit_upsert")
        await repo.save(bp)

        updated = bp.model_copy(update={"description": "Updated description."})
        await repo.save(updated)

        fetched = await repo.get(bp.id)
        assert fetched is not None
        assert fetched.description == "Updated description."

    async def test_validation_round_trips(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        bp = _blueprint(
            blueprint_id="bp-val",
            name="synthorg_textkit_val",
            state=ToolBlueprintState.ACTIVE,
        )
        await repo.save(bp)

        fetched = await repo.get(bp.id)
        assert fetched is not None
        assert fetched.validation is not None
        assert fetched.validation.passed is True
        assert fetched.validation.candidate_score == 101
        assert fetched.validated_at is not None
        assert fetched.activated_at is not None

    async def test_query_by_state(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(
            _blueprint(
                blueprint_id="p",
                name="synthorg_textkit_pending",
                state=ToolBlueprintState.PENDING,
            )
        )
        await repo.save(
            _blueprint(
                blueprint_id="a",
                name="synthorg_textkit_active",
                capability="textkit:active",
                state=ToolBlueprintState.ACTIVE,
            )
        )

        active = await repo.query(
            ToolBlueprintFilterSpec(state=ToolBlueprintState.ACTIVE)
        )
        ids = {bp.id for bp in active}
        assert "a" in ids
        assert "p" not in ids

    async def test_query_by_capability(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(
            _blueprint(
                blueprint_id="c1",
                name="synthorg_alpha_one",
                capability="alpha:one",
            )
        )
        await repo.save(
            _blueprint(
                blueprint_id="c2",
                name="synthorg_beta_two",
                capability="beta:two",
            )
        )

        rows = await repo.query(
            ToolBlueprintFilterSpec(capability=NotBlankStr("alpha:one"))
        )
        ids = {bp.id for bp in rows}
        assert ids == {"c1"}

    async def test_count(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_blueprint(blueprint_id="n1", name="synthorg_textkit_n1"))
        await repo.save(
            _blueprint(
                blueprint_id="n2",
                name="synthorg_textkit_n2",
                capability="textkit:n2",
            )
        )
        assert await repo.count(ToolBlueprintFilterSpec()) >= 2

    async def test_transition_if_stamps_timestamp(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        bp = _blueprint(blueprint_id="bp-trans", name="synthorg_textkit_trans")
        await repo.save(bp)
        validated_at = _NOW + timedelta(minutes=1)

        ok = await repo.transition_if(
            bp.id,
            from_state=ToolBlueprintState.PENDING,
            to_state=ToolBlueprintState.VALIDATED,
            validated_at=validated_at,
        )
        assert ok is True

        fetched = await repo.get(bp.id)
        assert fetched is not None
        assert fetched.state is ToolBlueprintState.VALIDATED
        assert fetched.validated_at is not None
        assert fetched.validated_at == validated_at

    async def test_transition_if_mismatch_returns_false(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        bp = _blueprint(blueprint_id="bp-mismatch", name="synthorg_textkit_mismatch")
        await repo.save(bp)

        ok = await repo.transition_if(
            bp.id,
            from_state=ToolBlueprintState.ACTIVE,
            to_state=ToolBlueprintState.RETIRED,
            retired_at=_NOW,
        )
        assert ok is False
        fetched = await repo.get(bp.id)
        assert fetched is not None
        assert fetched.state is ToolBlueprintState.PENDING

    async def test_transition_if_missing_returns_false(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        ok = await repo.transition_if(
            NotBlankStr("bp-trans-missing"),
            from_state=ToolBlueprintState.PENDING,
            to_state=ToolBlueprintState.VALIDATED,
            validated_at=_NOW,
        )
        assert ok is False

    async def test_transition_if_rejects_unknown_key(
        self, backend: PersistenceBackend
    ) -> None:
        from synthorg.core.persistence_errors import QueryError

        repo = _repo(backend)
        bp = _blueprint(blueprint_id="bp-badkey", name="synthorg_textkit_badkey")
        await repo.save(bp)

        with pytest.raises(QueryError):
            await repo.transition_if(
                bp.id,
                from_state=ToolBlueprintState.PENDING,
                to_state=ToolBlueprintState.VALIDATED,
                bogus_key=_NOW,
            )

    async def test_delete_returns_true_then_false(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        bp = _blueprint(blueprint_id="bp-del", name="synthorg_textkit_del")
        await repo.save(bp)
        assert await repo.get(bp.id) is not None

        assert await repo.delete(bp.id) is True
        assert await repo.get(bp.id) is None
        assert await repo.delete(bp.id) is False

    async def test_protocol_runtime_check(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        assert isinstance(repo, DynamicToolRepository)
