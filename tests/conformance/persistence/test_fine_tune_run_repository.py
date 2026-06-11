"""Conformance tests for ``FineTuneRunRepository`` (SQLite + Postgres)."""

from datetime import UTC, datetime

import pytest

from synthorg.memory.embedding.fine_tune import FineTuneStage
from synthorg.memory.embedding.fine_tune_models import (
    FineTuneRun,
    FineTuneRunConfig,
)
from synthorg.persistence.protocol import PersistenceBackend
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.integration


def _cfg() -> FineTuneRunConfig:
    return FineTuneRunConfig(
        source_dir="/docs",
        base_model="test-model",
        output_dir="/out",
    )


def _run(
    run_id: str = "run-1",
    stage: FineTuneStage = FineTuneStage.GENERATING_DATA,
    *,
    started_at: datetime | None = None,
    **overrides: object,
) -> FineTuneRun:
    """Build a ``FineTuneRun`` with sensible defaults for tests."""
    base_ts = started_at or datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    defaults: dict[str, object] = {
        "id": as_uuid(run_id),
        "stage": stage,
        "config": _cfg(),
        "started_at": base_ts,
        "updated_at": base_ts,
    }
    if stage in {FineTuneStage.COMPLETE, FineTuneStage.FAILED}:
        defaults.setdefault("completed_at", base_ts)
    if stage == FineTuneStage.FAILED:
        defaults.setdefault("error", "test failure")
    defaults.update(overrides)
    return FineTuneRun(**defaults)  # type: ignore[arg-type]


class TestFineTuneRunRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        run = _run()
        await backend.fine_tune_runs.save(run)

        fetched = await backend.fine_tune_runs.get(sid("run-1"))
        assert fetched is not None
        assert fetched.id == as_uuid("run-1")
        assert fetched.stage is FineTuneStage.GENERATING_DATA
        assert fetched.config.source_dir == "/docs"
        assert fetched.started_at == run.started_at
        assert fetched.updated_at == run.updated_at
        assert fetched.completed_at is None
        assert fetched.stages_completed == ()

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.fine_tune_runs.get("ghost") is None

    async def test_save_run_upsert(self, backend: PersistenceBackend) -> None:
        run = _run(progress=0.1)
        await backend.fine_tune_runs.save(run)

        updated = run.model_copy(update={"progress": 0.5})
        await backend.fine_tune_runs.save(updated)

        fetched = await backend.fine_tune_runs.get(sid("run-1"))
        assert fetched is not None
        assert fetched.progress == 0.5
        runs, total = await backend.fine_tune_runs.list_items_page()
        assert total == 1
        assert len(runs) == 1

    async def test_get_active_run_returns_only_active(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.fine_tune_runs.save(
            _run("r-active", FineTuneStage.TRAINING),
        )
        await backend.fine_tune_runs.save(
            _run("r-done", FineTuneStage.COMPLETE),
        )

        active = await backend.fine_tune_runs.get_active_run()
        assert active is not None
        assert active.id == as_uuid("r-active")

    async def test_get_active_run_none_when_only_terminal(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.fine_tune_runs.save(
            _run("r-done", FineTuneStage.COMPLETE),
        )
        await backend.fine_tune_runs.save(
            _run("r-fail", FineTuneStage.FAILED),
        )

        assert await backend.fine_tune_runs.get_active_run() is None

    async def test_get_active_run_returns_most_recent(
        self, backend: PersistenceBackend
    ) -> None:
        # Two simultaneously-active runs (stage in active set);
        # repository must return the one with the highest started_at.
        older = _run(
            "r-old",
            FineTuneStage.TRAINING,
            started_at=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        )
        newer = _run(
            "r-new",
            FineTuneStage.EVALUATING,
            started_at=datetime(2026, 1, 1, 14, 0, tzinfo=UTC),
        )
        await backend.fine_tune_runs.save(older)
        await backend.fine_tune_runs.save(newer)

        active = await backend.fine_tune_runs.get_active_run()
        assert active is not None
        assert active.id == as_uuid("r-new")

    async def test_list_runs_empty(self, backend: PersistenceBackend) -> None:
        runs, total = await backend.fine_tune_runs.list_items_page()
        assert runs == ()
        assert total == 0

    async def test_list_runs_orders_by_started_at_desc(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.fine_tune_runs.save(
            _run("r-old", started_at=datetime(2026, 1, 1, tzinfo=UTC)),
        )
        await backend.fine_tune_runs.save(
            _run("r-mid", started_at=datetime(2026, 2, 1, tzinfo=UTC)),
        )
        await backend.fine_tune_runs.save(
            _run("r-new", started_at=datetime(2026, 3, 1, tzinfo=UTC)),
        )

        runs, total = await backend.fine_tune_runs.list_items_page()
        assert total == 3
        assert [r.id for r in runs] == [
            as_uuid("r-new"),
            as_uuid("r-mid"),
            as_uuid("r-old"),
        ]

    async def test_list_runs_pagination(self, backend: PersistenceBackend) -> None:
        for i in range(5):
            await backend.fine_tune_runs.save(
                _run(
                    f"r{i}",
                    started_at=datetime(2026, 1, i + 1, tzinfo=UTC),
                ),
            )

        page1, total = await backend.fine_tune_runs.list_items_page(limit=2, offset=0)
        page2, _ = await backend.fine_tune_runs.list_items_page(limit=2, offset=2)
        page3, _ = await backend.fine_tune_runs.list_items_page(limit=2, offset=4)
        assert total == 5
        assert [r.id for r in page1] == [as_uuid("r4"), as_uuid("r3")]
        assert [r.id for r in page2] == [as_uuid("r2"), as_uuid("r1")]
        assert [r.id for r in page3] == [as_uuid("r0")]

    async def test_list_runs_clamps_limit_and_offset(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.fine_tune_runs.save(_run("r1"))

        # limit=0 must clamp up to 1; offset=-1 must clamp up to 0.
        runs, total = await backend.fine_tune_runs.list_items_page(limit=0, offset=-1)
        assert total == 1
        assert len(runs) == 1

    async def test_update_run_persists_all_mutable_fields(
        self, backend: PersistenceBackend
    ) -> None:
        run = _run()
        await backend.fine_tune_runs.save(run)

        updated = run.model_copy(
            update={
                "stage": FineTuneStage.TRAINING,
                "progress": 0.42,
                "stages_completed": (
                    "generating_data",
                    "mining_negatives",
                ),
                "updated_at": datetime(2026, 6, 1, tzinfo=UTC),
            },
        )
        await backend.fine_tune_runs.update_run(updated)

        fetched = await backend.fine_tune_runs.get(sid("run-1"))
        assert fetched is not None
        assert fetched.stage is FineTuneStage.TRAINING
        assert fetched.progress == 0.42
        assert fetched.stages_completed == (
            "generating_data",
            "mining_negatives",
        )
        assert fetched.updated_at == datetime(2026, 6, 1, tzinfo=UTC)

    async def test_update_run_to_terminal_stage(
        self, backend: PersistenceBackend
    ) -> None:
        run = _run()
        await backend.fine_tune_runs.save(run)

        terminal = run.model_copy(
            update={
                "stage": FineTuneStage.COMPLETE,
                "completed_at": datetime(2026, 7, 1, tzinfo=UTC),
                "stages_completed": (
                    "generating_data",
                    "mining_negatives",
                    "training",
                    "evaluating",
                    "deploying",
                ),
            },
        )
        await backend.fine_tune_runs.update_run(terminal)

        fetched = await backend.fine_tune_runs.get(sid("run-1"))
        assert fetched is not None
        assert fetched.stage is FineTuneStage.COMPLETE
        assert fetched.completed_at == datetime(2026, 7, 1, tzinfo=UTC)
        assert fetched.duration_seconds is not None

    async def test_mark_interrupted_transitions_active_runs(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.fine_tune_runs.save(
            _run("r-train", FineTuneStage.TRAINING),
        )
        await backend.fine_tune_runs.save(
            _run("r-eval", FineTuneStage.EVALUATING),
        )
        await backend.fine_tune_runs.save(
            _run("r-done", FineTuneStage.COMPLETE),
        )

        count = await backend.fine_tune_runs.mark_interrupted()
        assert count == 2

        train = await backend.fine_tune_runs.get(sid("r-train"))
        assert train is not None
        assert train.stage is FineTuneStage.FAILED
        assert train.error == "interrupted by restart"
        assert train.completed_at is not None

        ev = await backend.fine_tune_runs.get(sid("r-eval"))
        assert ev is not None
        assert ev.stage is FineTuneStage.FAILED

        done = await backend.fine_tune_runs.get(sid("r-done"))
        assert done is not None
        assert done.stage is FineTuneStage.COMPLETE

    async def test_mark_interrupted_single_transaction(
        self, backend: PersistenceBackend
    ) -> None:
        # All runs transitioned by a single call must share the same
        # ``updated_at`` / ``completed_at`` timestamp -- evidence that
        # the update ran inside one atomic transaction.  A per-row loop
        # (which would race with a concurrent mark_interrupted) would
        # fail this assertion.
        await backend.fine_tune_runs.save(
            _run("r-train", FineTuneStage.TRAINING),
        )
        await backend.fine_tune_runs.save(
            _run("r-eval", FineTuneStage.EVALUATING),
        )

        await backend.fine_tune_runs.mark_interrupted()

        train = await backend.fine_tune_runs.get(sid("r-train"))
        ev = await backend.fine_tune_runs.get(sid("r-eval"))
        assert train is not None
        assert ev is not None
        assert train.updated_at == ev.updated_at
        assert train.completed_at == ev.completed_at
        assert train.error == ev.error == "interrupted by restart"

    async def test_mark_interrupted_idempotent(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.fine_tune_runs.save(
            _run("r-train", FineTuneStage.TRAINING),
        )

        first = await backend.fine_tune_runs.mark_interrupted()
        second = await backend.fine_tune_runs.mark_interrupted()
        assert first == 1
        assert second == 0

    async def test_mark_interrupted_no_active_runs(
        self, backend: PersistenceBackend
    ) -> None:
        assert await backend.fine_tune_runs.mark_interrupted() == 0

    async def test_save_run_preserves_stages_completed_array(
        self, backend: PersistenceBackend
    ) -> None:
        # Round-trip a non-trivial JSON array (TEXT in SQLite, JSONB in PG).
        run = _run(
            stages_completed=(
                "generating_data",
                "mining_negatives",
                "training",
            ),
        )
        await backend.fine_tune_runs.save(run)

        fetched = await backend.fine_tune_runs.get(sid("run-1"))
        assert fetched is not None
        assert fetched.stages_completed == (
            "generating_data",
            "mining_negatives",
            "training",
        )

    async def test_save_run_preserves_config_round_trip(
        self, backend: PersistenceBackend
    ) -> None:
        # Round-trip a non-default config (TEXT in SQLite, JSONB in PG).
        cfg = FineTuneRunConfig(
            source_dir="/custom/docs",
            base_model="model-a",
            output_dir="/custom/out",
            epochs=7,
            learning_rate=2e-4,
            temperature=0.05,
            top_k=8,
            batch_size=64,
            validation_split=0.2,
        )
        run = _run(config=cfg)
        await backend.fine_tune_runs.save(run)

        fetched = await backend.fine_tune_runs.get(sid("run-1"))
        assert fetched is not None
        assert fetched.config == cfg
