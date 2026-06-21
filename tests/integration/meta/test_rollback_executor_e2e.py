"""End-to-end rollback-executor write-path tests over a real SQLite backend.

Proves the wired rollback WRITE path functions against durable stores:

* ``restore_prompt`` writes a principle-override row AND a subsequent prompt
  build overlays the restored text.
* ``remove_principle`` deletes an active principle created by a prompt apply
  AND the next prompt build no longer surfaces it.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from synthorg.core.pagination import collect_all
from synthorg.core.types import NotBlankStr
from synthorg.engine.strategy.active_principle import (
    ActivePrinciple,
    PrincipleEvolutionMode,
    ScopeKind,
)
from synthorg.engine.strategy.active_principle_provider import (
    CachedActivePrincipleProvider,
)
from synthorg.engine.strategy.models import ConstitutionalPrincipleConfig
from synthorg.engine.strategy.principle_override_provider import (
    CachedPrincipleOverrideProvider,
)
from synthorg.engine.strategy.principles import load_and_merge, load_pack
from synthorg.meta.factory import build_rollback_executor
from synthorg.meta.models import RollbackOperation
from synthorg.meta.rollout.inverse_dispatch import (
    ArchitectureMutator,
    CodeMutator,
    ConfigMutator,
)
from synthorg.meta.rollout.mutators import (
    ActivePrincipleRemovalMutator,
    PrincipleOverridePromptMutator,
)
from synthorg.persistence import migrations
from synthorg.persistence.config import SQLiteConfig
from synthorg.persistence.db_handle import sqlite_connection
from synthorg.persistence.sqlite.active_principle_repo import (
    SQLiteActivePrincipleRepository,
)
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend
from tests._shared import as_uuid, mock_of

pytestmark = pytest.mark.integration


@pytest.fixture
async def sqlite_backend(tmp_path: Path) -> AsyncIterator[SQLitePersistenceBackend]:
    """Yield a connected, migrated SQLite backend for the rollback path."""
    db_path = tmp_path / "rollback_e2e.db"
    rev_path = migrations.copy_revisions(tmp_path / "revisions", backend="sqlite")
    await migrations.migrate_apply(
        migrations.to_sqlite_url(str(db_path)),
        revisions_path=rev_path,
    )
    backend = SQLitePersistenceBackend(SQLiteConfig(path=str(db_path)))
    await backend.connect()
    try:
        yield backend
    finally:
        await backend.disconnect()


async def test_restore_prompt_writes_override_and_overlays(
    sqlite_backend: SQLitePersistenceBackend,
) -> None:
    """``restore_prompt`` persists an override row and the build overlays it."""
    override_repo = sqlite_backend.principle_overrides

    async def _load() -> dict[str, str]:
        rows = await collect_all(
            lambda limit, offset: override_repo.list_items(limit=limit, offset=offset)
        )
        return {row.scope: row.text for row in rows}

    override_provider = CachedPrincipleOverrideProvider(loader=_load)
    await override_provider.refresh()
    prompt_mutator = PrincipleOverridePromptMutator(
        override_repo=override_repo,
        on_override_written=override_provider.refresh,
    )
    executor = build_rollback_executor(
        config_mutator=mock_of[ConfigMutator](),
        prompt_mutator=prompt_mutator,
        architecture_mutator=mock_of[ArchitectureMutator](),
        code_mutator=mock_of[CodeMutator](),
    )

    pack = load_pack("default")
    target_id = pack.principles[0].id
    restored_text = "Restored: prioritise correctness over speed."

    result = await executor.execute_operations(
        (
            RollbackOperation(
                operation_type="restore_prompt",
                target=target_id,
                previous_value=restored_text,
                description="Restore the prior principle text",
            ),
        ),
        proposal_id=as_uuid("prop-restore"),
    )

    assert result.success
    assert result.changes_applied == 1
    # The override row is durably written, keyed by the pack principle id.
    stored = await override_repo.get(NotBlankStr(target_id))
    assert stored is not None
    assert stored.text == restored_text

    # A subsequent prompt build overlays the restored text on that principle.
    config = ConstitutionalPrincipleConfig(pack="default")
    principles = load_and_merge(config, principle_overrides=override_provider)
    overlaid = next(p for p in principles if p.id == target_id)
    assert overlaid.text == restored_text


async def test_remove_principle_deletes_active_and_drops_from_build(
    sqlite_backend: SQLitePersistenceBackend,
) -> None:
    """``remove_principle`` deletes the active principle and drops it from builds."""
    repo = SQLiteActivePrincipleRepository(
        sqlite_connection(sqlite_backend),
        write_context=sqlite_backend.write_context,
    )
    now = datetime.now(UTC)
    principle = ActivePrinciple(
        principle_text="Added by a prompt apply: double-check delegated work.",
        scope=NotBlankStr("all"),
        scope_kind=ScopeKind.ALL,
        evolution_mode=PrincipleEvolutionMode.ORG_WIDE,
        created_at=now,
        updated_at=now,
    )
    await repo.save(principle)

    async def _load() -> tuple[ActivePrinciple, ...]:
        return await collect_all(
            lambda limit, offset: repo.list_items(limit=limit, offset=offset)
        )

    active_provider = CachedActivePrincipleProvider(loader=_load)
    await active_provider.refresh()
    assert len(active_provider.snapshot()) == 1

    removal_mutator = ActivePrincipleRemovalMutator(
        repo=repo,
        on_principle_removed=active_provider.refresh,
    )
    executor = build_rollback_executor(
        prompt_mutator=mock_of[PrincipleOverridePromptMutator](),
        principle_removal_mutator=removal_mutator,
        config_mutator=mock_of[ConfigMutator](),
        architecture_mutator=mock_of[ArchitectureMutator](),
        code_mutator=mock_of[CodeMutator](),
    )

    result = await executor.execute_operations(
        (
            RollbackOperation(
                operation_type="remove_principle",
                target=str(principle.id),
                description="Remove the active principle this apply created",
            ),
        ),
        proposal_id=as_uuid("prop-remove"),
    )

    assert result.success
    assert result.changes_applied == 1
    assert await repo.get(NotBlankStr(str(principle.id))) is None
    # The refresh hook fired, so the build snapshot no longer surfaces it.
    assert active_provider.snapshot() == ()
