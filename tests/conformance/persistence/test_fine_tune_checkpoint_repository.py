"""Conformance tests for ``FineTuneCheckpointRepository`` (SQLite + Postgres).

Distinct from ``test_checkpoint_repository.py``, which covers the
unrelated agent-execution ``CheckpointRepository`` (engine layer).
"""

import json
from datetime import UTC, datetime

import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.memory.embedding.fine_tune import FineTuneStage
from synthorg.memory.embedding.fine_tune_models import (
    CheckpointRecord,
    EvalMetrics,
    FineTuneRun,
    FineTuneRunConfig,
)
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration


def _cfg() -> FineTuneRunConfig:
    return FineTuneRunConfig(
        source_dir="/docs",
        base_model="test-model",
        output_dir="/out",
    )


def _run(run_id: str = "run-1") -> FineTuneRun:
    """Parent run for FK satisfaction; checkpoints reference run_id."""
    ts = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    return FineTuneRun(
        id=run_id,
        stage=FineTuneStage.TRAINING,
        config=_cfg(),
        started_at=ts,
        updated_at=ts,
    )


def _checkpoint(
    cp_id: str = "cp-1",
    run_id: str = "run-1",
    *,
    created_at: datetime | None = None,
    **overrides: object,
) -> CheckpointRecord:
    base_ts = created_at or datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    defaults: dict[str, object] = {
        "id": cp_id,
        "run_id": run_id,
        "model_path": "/models/cp",
        "base_model": "test-model",
        "doc_count": 100,
        "size_bytes": 4096,
        "created_at": base_ts,
    }
    defaults.update(overrides)
    return CheckpointRecord(**defaults)  # type: ignore[arg-type]


class TestFineTuneCheckpointRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        await backend.fine_tune_runs.save_run(_run())
        cp = _checkpoint()
        await backend.fine_tune_checkpoints.save_checkpoint(cp)

        fetched = await backend.fine_tune_checkpoints.get_checkpoint("cp-1")
        assert fetched is not None
        assert fetched.id == "cp-1"
        assert fetched.run_id == "run-1"
        assert fetched.doc_count == 100
        assert fetched.size_bytes == 4096
        assert fetched.is_active is False
        assert fetched.eval_metrics is None
        assert fetched.created_at == cp.created_at

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.fine_tune_checkpoints.get_checkpoint("ghost") is None

    async def test_save_checkpoint_upsert(self, backend: PersistenceBackend) -> None:
        await backend.fine_tune_runs.save_run(_run())
        cp = _checkpoint(doc_count=50)
        await backend.fine_tune_checkpoints.save_checkpoint(cp)

        updated = cp.model_copy(update={"doc_count": 200, "size_bytes": 8192})
        await backend.fine_tune_checkpoints.save_checkpoint(updated)

        fetched = await backend.fine_tune_checkpoints.get_checkpoint("cp-1")
        assert fetched is not None
        assert fetched.doc_count == 200
        assert fetched.size_bytes == 8192
        _, total = await backend.fine_tune_checkpoints.list_checkpoints()
        assert total == 1

    async def test_list_checkpoints_empty(self, backend: PersistenceBackend) -> None:
        rows, total = await backend.fine_tune_checkpoints.list_checkpoints()
        assert rows == ()
        assert total == 0

    async def test_list_checkpoints_orders_by_created_at_desc(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.fine_tune_runs.save_run(_run())
        await backend.fine_tune_checkpoints.save_checkpoint(
            _checkpoint(
                "cp-old",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        )
        await backend.fine_tune_checkpoints.save_checkpoint(
            _checkpoint(
                "cp-mid",
                created_at=datetime(2026, 2, 1, tzinfo=UTC),
            ),
        )
        await backend.fine_tune_checkpoints.save_checkpoint(
            _checkpoint(
                "cp-new",
                created_at=datetime(2026, 3, 1, tzinfo=UTC),
            ),
        )

        cps, total = await backend.fine_tune_checkpoints.list_checkpoints()
        assert total == 3
        assert [c.id for c in cps] == ["cp-new", "cp-mid", "cp-old"]

    async def test_list_checkpoints_pagination(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.fine_tune_runs.save_run(_run())
        for i in range(5):
            await backend.fine_tune_checkpoints.save_checkpoint(
                _checkpoint(
                    f"cp-{i}",
                    created_at=datetime(2026, 1, i + 1, tzinfo=UTC),
                ),
            )

        page, total = await backend.fine_tune_checkpoints.list_checkpoints(
            limit=2, offset=1
        )
        assert total == 5
        assert [c.id for c in page] == ["cp-3", "cp-2"]

    async def test_set_active_makes_exactly_one_active(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.fine_tune_runs.save_run(_run())
        await backend.fine_tune_checkpoints.save_checkpoint(_checkpoint("cp-1"))
        await backend.fine_tune_checkpoints.save_checkpoint(_checkpoint("cp-2"))

        await backend.fine_tune_checkpoints.set_active("cp-2")

        active = await backend.fine_tune_checkpoints.get_active_checkpoint()
        assert active is not None
        assert active.id == "cp-2"
        cp1 = await backend.fine_tune_checkpoints.get_checkpoint("cp-1")
        assert cp1 is not None
        assert cp1.is_active is False

    async def test_set_active_switches_active_row(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.fine_tune_runs.save_run(_run())
        await backend.fine_tune_checkpoints.save_checkpoint(_checkpoint("cp-1"))
        await backend.fine_tune_checkpoints.save_checkpoint(_checkpoint("cp-2"))
        await backend.fine_tune_checkpoints.set_active("cp-1")
        await backend.fine_tune_checkpoints.set_active("cp-2")

        active = await backend.fine_tune_checkpoints.get_active_checkpoint()
        assert active is not None
        assert active.id == "cp-2"
        cp1 = await backend.fine_tune_checkpoints.get_checkpoint("cp-1")
        assert cp1 is not None
        assert cp1.is_active is False

    async def test_set_active_unknown_id_raises(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.fine_tune_runs.save_run(_run())
        with pytest.raises(QueryError, match="not found"):
            await backend.fine_tune_checkpoints.set_active("ghost")

    async def test_deactivate_all(self, backend: PersistenceBackend) -> None:
        await backend.fine_tune_runs.save_run(_run())
        await backend.fine_tune_checkpoints.save_checkpoint(_checkpoint("cp-1"))
        await backend.fine_tune_checkpoints.save_checkpoint(_checkpoint("cp-2"))
        await backend.fine_tune_checkpoints.set_active("cp-2")

        await backend.fine_tune_checkpoints.deactivate_all()

        assert await backend.fine_tune_checkpoints.get_active_checkpoint() is None
        cps, _ = await backend.fine_tune_checkpoints.list_checkpoints()
        for cp in cps:
            assert cp.is_active is False

    async def test_deactivate_all_idempotent(self, backend: PersistenceBackend) -> None:
        await backend.fine_tune_runs.save_run(_run())
        await backend.fine_tune_checkpoints.save_checkpoint(_checkpoint("cp-1"))

        await backend.fine_tune_checkpoints.deactivate_all()
        # Calling twice must not raise.
        await backend.fine_tune_checkpoints.deactivate_all()

        cp = await backend.fine_tune_checkpoints.get_checkpoint("cp-1")
        assert cp is not None
        assert cp.is_active is False

    async def test_delete_checkpoint(self, backend: PersistenceBackend) -> None:
        await backend.fine_tune_runs.save_run(_run())
        await backend.fine_tune_checkpoints.save_checkpoint(_checkpoint("cp-1"))

        await backend.fine_tune_checkpoints.delete_checkpoint("cp-1")
        assert await backend.fine_tune_checkpoints.get_checkpoint("cp-1") is None

    async def test_delete_missing_checkpoint_silent(
        self, backend: PersistenceBackend
    ) -> None:
        # Mirror SQLite semantics: deleting a non-existent checkpoint
        # is a no-op (returns silently), not an error.
        await backend.fine_tune_checkpoints.delete_checkpoint("ghost")

    async def test_delete_active_checkpoint_raises(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.fine_tune_runs.save_run(_run())
        await backend.fine_tune_checkpoints.save_checkpoint(_checkpoint("cp-1"))
        await backend.fine_tune_checkpoints.set_active("cp-1")

        with pytest.raises(QueryError, match="active"):
            await backend.fine_tune_checkpoints.delete_checkpoint("cp-1")

        # Active row still present after the failed delete.
        cp = await backend.fine_tune_checkpoints.get_checkpoint("cp-1")
        assert cp is not None
        assert cp.is_active is True

    async def test_get_active_checkpoint_returns_none_when_no_active(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.fine_tune_runs.save_run(_run())
        await backend.fine_tune_checkpoints.save_checkpoint(_checkpoint("cp-1"))

        assert await backend.fine_tune_checkpoints.get_active_checkpoint() is None

    async def test_eval_metrics_round_trip(self, backend: PersistenceBackend) -> None:
        await backend.fine_tune_runs.save_run(_run())
        metrics = EvalMetrics(
            ndcg_at_10=0.6,
            recall_at_10=0.7,
            base_ndcg_at_10=0.5,
            base_recall_at_10=0.6,
        )
        await backend.fine_tune_checkpoints.save_checkpoint(
            _checkpoint(eval_metrics=metrics),
        )

        fetched = await backend.fine_tune_checkpoints.get_checkpoint("cp-1")
        assert fetched is not None
        assert fetched.eval_metrics is not None
        assert fetched.eval_metrics.ndcg_at_10 == 0.6
        assert fetched.eval_metrics.recall_at_10 == 0.7
        assert fetched.eval_metrics.base_ndcg_at_10 == 0.5
        assert fetched.eval_metrics.base_recall_at_10 == 0.6

    async def test_save_checkpoint_nonexistent_run_raises(
        self, backend: PersistenceBackend
    ) -> None:
        # FK: fine_tune_checkpoints.run_id -> fine_tune_runs.id
        # ON DELETE CASCADE.  Inserting with an unknown run_id must
        # fail at the DB layer on both backends; the repository wraps
        # the driver error as QueryError.
        cp = _checkpoint(run_id="run-ghost")
        with pytest.raises(QueryError):
            await backend.fine_tune_checkpoints.save_checkpoint(cp)
        # Partial insert must not leak.
        assert await backend.fine_tune_checkpoints.get_checkpoint(cp.id) is None

    async def test_list_checkpoints_clamps_limit_and_offset(
        self, backend: PersistenceBackend
    ) -> None:
        # Mirrors the run-repo clamping test: limit=0 must clamp up to 1;
        # offset=-1 must clamp up to 0.
        await backend.fine_tune_runs.save_run(_run())
        await backend.fine_tune_checkpoints.save_checkpoint(_checkpoint("cp-1"))

        rows, total = await backend.fine_tune_checkpoints.list_checkpoints(
            limit=0, offset=-1
        )
        assert total == 1
        assert len(rows) == 1

    async def test_backup_config_json_round_trip(
        self, backend: PersistenceBackend
    ) -> None:
        # backup_config_json is TEXT on SQLite, JSONB on Postgres.
        # The repository must accept a JSON string from the model and
        # return the same JSON string after a round-trip.
        payload = '{"deployed_at": "2026-01-01T00:00:00Z", "version": 7}'
        await backend.fine_tune_runs.save_run(_run())
        await backend.fine_tune_checkpoints.save_checkpoint(
            _checkpoint(backup_config_json=payload),
        )

        fetched = await backend.fine_tune_checkpoints.get_checkpoint("cp-1")
        assert fetched is not None
        assert fetched.backup_config_json is not None
        # Compare as JSON (Postgres JSONB normalises whitespace + key
        # ordering; the model field is a string so we re-parse to compare
        # semantic equality rather than byte equality).
        assert json.loads(fetched.backup_config_json) == json.loads(payload)

    @pytest.mark.parametrize(
        "payload",
        [
            '"just-a-string"',  # JSON scalar string
            "42",  # JSON number
            "true",  # JSON boolean
            "[1, 2, 3]",  # JSON array
        ],
        ids=["string-scalar", "number", "bool", "array"],
    )
    async def test_backup_config_json_scalar_round_trip(
        self, backend: PersistenceBackend, payload: str
    ) -> None:
        # JSONB can hold any JSON type, not just objects.  Postgres
        # returns the decoded Python value from the JSONB loader
        # (``str`` for a JSON string scalar, ``int``/``float`` for a
        # number, ``list`` for an array, ...), so the read path must
        # re-serialise unconditionally.  Previously the ``str`` fast
        # path corrupted JSON scalar strings -- JSONB ``"foo"`` came
        # back as the Python ``str`` ``foo`` and got stored in the
        # model verbatim, which is not valid JSON text and broke any
        # ``json.loads()`` consumer (``MemoryService.rollback_checkpoint``
        # in particular).
        await backend.fine_tune_runs.save_run(_run())
        await backend.fine_tune_checkpoints.save_checkpoint(
            _checkpoint(backup_config_json=payload),
        )

        fetched = await backend.fine_tune_checkpoints.get_checkpoint("cp-1")
        assert fetched is not None
        assert fetched.backup_config_json is not None
        # The round-tripped string must itself parse as valid JSON
        # *and* decode to the same value as the input.
        assert json.loads(fetched.backup_config_json) == json.loads(payload)
