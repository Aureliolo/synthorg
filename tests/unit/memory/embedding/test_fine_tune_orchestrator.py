"""Tests for FineTuneOrchestrator."""

import asyncio
import contextlib
from collections.abc import AsyncGenerator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import aiosqlite
import pytest

from synthorg.core.domain_errors import FineTuneRunActiveError
from synthorg.core.types import NotBlankStr
from synthorg.memory.embedding.cancellation import CancellationToken
from synthorg.memory.embedding.fine_tune import FineTuneStage
from synthorg.memory.embedding.fine_tune_models import (
    EvalMetrics,
    FineTuneDataSourceType,
    FineTuneRequest,
    FineTuneRun,
    FineTuneRunConfig,
)
from synthorg.memory.embedding.fine_tune_orchestrator import (
    _PROGRESS_THROTTLE_SEC,
    FineTuneOrchestrator,
)
from synthorg.memory.embedding.training_sources import (
    QueryPassagePair,
    TrainingPairSource,
)
from synthorg.memory.errors import FineTuneCancelledError
from synthorg.persistence.sqlite.fine_tune_repo import (
    SQLiteFineTuneCheckpointRepository,
    SQLiteFineTuneRunRepository,
)
from tests._shared import as_uuid, sid
from tests._shared.fake_clock import FakeClock
from tests._shared.persistence import make_private_write_context

_SCHEMA_PATH = Path("src/synthorg/persistence/sqlite/schema.sql")


@pytest.fixture
async def db() -> AsyncGenerator[aiosqlite.Connection]:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    schema = _SCHEMA_PATH.read_text()  # noqa: ASYNC240
    await conn.executescript(schema)
    yield conn
    await conn.close()


@pytest.fixture
def run_repo(db: aiosqlite.Connection) -> SQLiteFineTuneRunRepository:
    return SQLiteFineTuneRunRepository(db, write_context=make_private_write_context())


@pytest.fixture
def cp_repo(
    db: aiosqlite.Connection,
) -> SQLiteFineTuneCheckpointRepository:
    return SQLiteFineTuneCheckpointRepository(
        db, write_context=make_private_write_context()
    )


@pytest.fixture
def orchestrator(
    run_repo: SQLiteFineTuneRunRepository,
    cp_repo: SQLiteFineTuneCheckpointRepository,
) -> FineTuneOrchestrator:
    return FineTuneOrchestrator(
        run_repo=run_repo,
        checkpoint_repo=cp_repo,
    )


def _request(tmp_path: Path) -> FineTuneRequest:
    """Build a FineTuneRequest with synthetic POSIX paths.

    Creates real files under *tmp_path* for diagnostics, but uses
    synthetic ``/test/src`` and ``/test/out`` paths in the returned
    ``FineTuneRequest`` because the orchestrator runs with mocked
    stage functions that bypass filesystem access.
    """
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    (src / "doc.txt").write_text("Test content for training. " * 50)
    # FineTuneRequest rejects Windows paths (drive letters).
    # Use synthetic POSIX paths -- the orchestrator mock doesn't
    # hit the filesystem for stages 2-5.
    return FineTuneRequest(
        source_dir="/test/src",
        output_dir="/test/out",
    )


# -- Basic lifecycle --------------------------------------------------


@pytest.mark.unit
class TestOrchestratorLifecycle:
    def test_not_running_initially(
        self,
        orchestrator: FineTuneOrchestrator,
    ) -> None:
        assert orchestrator.is_running is False
        assert orchestrator.current_run is None

    async def test_start_creates_run(
        self,
        orchestrator: FineTuneOrchestrator,
        run_repo: SQLiteFineTuneRunRepository,
        tmp_path: Path,
    ) -> None:
        req = _request(tmp_path)
        # Mock stage functions to return immediately.
        with _mock_all_stages():
            run = await orchestrator.start(req)
            assert isinstance(run.id, UUID)
            assert run.stage == FineTuneStage.GENERATING_DATA
            # Wait for background task to complete.
            if orchestrator._current_task is not None:
                await orchestrator._current_task

        # Run should be persisted.
        fetched = await run_repo.get(str(run.id))
        assert fetched is not None

    async def test_double_start_raises(
        self,
        orchestrator: FineTuneOrchestrator,
        tmp_path: Path,
    ) -> None:
        req = _request(tmp_path)
        with _mock_all_stages(block=True):
            await orchestrator.start(req)
            with pytest.raises(FineTuneRunActiveError, match="already active"):
                await orchestrator.start(req)
            # Clean up the blocking task.
            await orchestrator.cancel()
            if orchestrator._current_task is not None:
                with contextlib.suppress(
                    asyncio.CancelledError,
                    FineTuneCancelledError,
                ):
                    await orchestrator._current_task


# -- Cancellation -----------------------------------------------------


@pytest.mark.unit
class TestOrchestratorCancellation:
    async def test_cancel_stops_run(
        self,
        orchestrator: FineTuneOrchestrator,
        run_repo: SQLiteFineTuneRunRepository,
        tmp_path: Path,
    ) -> None:
        req = _request(tmp_path)
        with _mock_all_stages(block=True):
            run = await orchestrator.start(req)
            # Yield to let the task start.
            await asyncio.sleep(0)
            await orchestrator.cancel()
            if orchestrator._current_task is not None:
                with contextlib.suppress(
                    asyncio.CancelledError,
                    FineTuneCancelledError,
                ):
                    await orchestrator._current_task

        # Run should be marked as failed.
        fetched = await run_repo.get(str(run.id))
        assert fetched is not None
        assert fetched.stage == FineTuneStage.FAILED


# -- Startup recovery ------------------------------------------------


@pytest.mark.unit
class TestOrchestratorRecovery:
    async def test_recover_interrupted(
        self,
        orchestrator: FineTuneOrchestrator,
        run_repo: SQLiteFineTuneRunRepository,
    ) -> None:
        now = datetime.now(tz=UTC)
        run = FineTuneRun(
            id=as_uuid("stale-run"),
            stage=FineTuneStage.TRAINING,
            progress=0.5,
            config=FineTuneRunConfig(
                source_dir="/docs",
                base_model="test-model",
                output_dir="/out",
            ),
            started_at=now,
            updated_at=now,
        )
        await run_repo.save(run)
        count = await orchestrator.recover_interrupted()
        assert count == 1
        fetched = await run_repo.get(sid("stale-run"))
        assert fetched is not None
        assert fetched.stage == FineTuneStage.FAILED


# -- Progress throttle (Clock seam) ----------------------------------


@pytest.mark.unit
class TestProgressThrottleClockSeam:
    """FakeClock controls when ``_make_progress_cb`` lets emits through."""

    async def test_throttle_uses_injected_clock(
        self,
        run_repo: SQLiteFineTuneRunRepository,
        cp_repo: SQLiteFineTuneCheckpointRepository,
    ) -> None:
        fake = FakeClock()
        orchestrator = FineTuneOrchestrator(
            run_repo=run_repo,
            checkpoint_repo=cp_repo,
            clock=fake,
        )
        now = datetime.now(tz=UTC)
        run = FineTuneRun(
            id=as_uuid("throttle-run"),
            stage=FineTuneStage.TRAINING,
            progress=0.0,
            config=FineTuneRunConfig(
                source_dir="/docs",
                base_model="test-model",
                output_dir="/out",
            ),
            started_at=now,
            updated_at=now,
        )
        emit_count = 0

        def _spy_emit(event_type: str, _run: FineTuneRun) -> None:
            nonlocal emit_count
            emit_count += 1

        with patch.object(orchestrator, "_emit_ws", side_effect=_spy_emit):
            cb = orchestrator._make_progress_cb(run)
            # First call: clock.monotonic()=0, last_emit=0, throttled.
            cb(0.05)
            await asyncio.sleep(0)
            assert emit_count == 0
            # Below the throttle window: still throttled.
            fake.advance(_PROGRESS_THROTTLE_SEC * 0.5)
            cb(0.10)
            await asyncio.sleep(0)
            assert emit_count == 0
            # Crossing the boundary: emit lands.
            fake.advance(_PROGRESS_THROTTLE_SEC * 0.6)
            cb(0.20)
            await asyncio.sleep(0)
            assert emit_count == 1
            # Subsequent call within the new window: throttled again.
            fake.advance(_PROGRESS_THROTTLE_SEC * 0.5)
            cb(0.25)
            await asyncio.sleep(0)
            assert emit_count == 1


# -- Status -----------------------------------------------------------


@pytest.mark.unit
class TestOrchestratorStatus:
    async def test_status_idle(
        self,
        orchestrator: FineTuneOrchestrator,
    ) -> None:
        status = await orchestrator.get_status()
        assert status.stage == FineTuneStage.IDLE
        assert status.run_id is None

    async def test_status_after_start(
        self,
        orchestrator: FineTuneOrchestrator,
        tmp_path: Path,
    ) -> None:
        req = _request(tmp_path)
        with _mock_all_stages():
            run = await orchestrator.start(req)
            if orchestrator._current_task is not None:
                await orchestrator._current_task
        status = await orchestrator.get_status()
        assert status.run_id == str(run.id)


# -- Promotion gate ---------------------------------------------------


@pytest.mark.unit
class TestPromotionGate:
    """Stage 5 promotes a checkpoint ONLY on a measured eval win."""

    async def _run_to_completion(
        self,
        orchestrator: FineTuneOrchestrator,
        req: FineTuneRequest,
        *,
        eval_metrics: EvalMetrics,
        deploy_calls: list[str],
    ) -> None:
        with _mock_all_stages(
            eval_metrics=eval_metrics,
            deploy_calls=deploy_calls,
        ):
            await orchestrator.start(req)
            if orchestrator._current_task is not None:
                await orchestrator._current_task

    async def test_measured_win_promotes_checkpoint(
        self,
        orchestrator: FineTuneOrchestrator,
        cp_repo: SQLiteFineTuneCheckpointRepository,
        tmp_path: Path,
    ) -> None:
        """A fine-tuned NDCG@10 above base by the margin activates + deploys."""
        deploy_calls: list[str] = []
        await self._run_to_completion(
            orchestrator,
            _request(tmp_path),
            eval_metrics=EvalMetrics(
                ndcg_at_10=0.6,
                recall_at_10=0.7,
                base_ndcg_at_10=0.5,
                base_recall_at_10=0.6,
            ),
            deploy_calls=deploy_calls,
        )

        checkpoints = await cp_repo.list_items()
        assert len(checkpoints) == 1
        assert checkpoints[0].is_active is True
        assert checkpoints[0].backup_config_json is not None
        assert len(deploy_calls) == 1
        active = await cp_repo.get_active_checkpoint()
        assert active is not None

    @pytest.mark.parametrize(
        ("ndcg", "base_ndcg"),
        [(0.5, 0.5), (0.4, 0.6)],
        ids=["exact-tie", "regression"],
    )
    async def test_tie_or_loss_records_inactive_and_skips_deploy(
        self,
        orchestrator: FineTuneOrchestrator,
        cp_repo: SQLiteFineTuneCheckpointRepository,
        tmp_path: Path,
        ndcg: float,
        base_ndcg: float,
    ) -> None:
        """A tie or regression records the checkpoint inactive, deploys nothing."""
        deploy_calls: list[str] = []
        await self._run_to_completion(
            orchestrator,
            _request(tmp_path),
            eval_metrics=EvalMetrics(
                ndcg_at_10=ndcg,
                recall_at_10=0.7,
                base_ndcg_at_10=base_ndcg,
                base_recall_at_10=0.6,
            ),
            deploy_calls=deploy_calls,
        )

        checkpoints = await cp_repo.list_items()
        assert len(checkpoints) == 1
        assert checkpoints[0].is_active is False
        # The deploy mechanism never ran: live embedder config is untouched.
        assert deploy_calls == []
        assert checkpoints[0].backup_config_json is None
        # The evaluated metrics are still recorded for operator audit.
        assert checkpoints[0].eval_metrics is not None
        active = await cp_repo.get_active_checkpoint()
        assert active is None

    async def test_run_completes_even_when_promotion_rejected(
        self,
        orchestrator: FineTuneOrchestrator,
        tmp_path: Path,
    ) -> None:
        """A rejected promotion still finishes the run cleanly (COMPLETE)."""
        req = _request(tmp_path)
        deploy_calls: list[str] = []
        await self._run_to_completion(
            orchestrator,
            req,
            eval_metrics=EvalMetrics(
                ndcg_at_10=0.5,
                recall_at_10=0.6,
                base_ndcg_at_10=0.5,
                base_recall_at_10=0.6,
            ),
            deploy_calls=deploy_calls,
        )
        status = await orchestrator.get_status()
        assert status.stage == FineTuneStage.COMPLETE


# -- Training-data source selection -----------------------------------


class _FakeTrainingDataSource:
    """Returns a fixed set of harvested pairs without touching real repos."""

    def __init__(self, pairs: tuple[QueryPassagePair, ...]) -> None:
        self._pairs = pairs
        self.collect_calls = 0

    @property
    def name(self) -> str:
        return "fake-trajectory"

    async def collect(
        self,
        cancellation: CancellationToken | None = None,
    ) -> tuple[QueryPassagePair, ...]:
        self.collect_calls += 1
        return self._pairs


_HARVESTED_PAIRS = (
    QueryPassagePair(
        query=NotBlankStr("Build checkout"),
        positive_passage=NotBlankStr("Resilient checkout deliverable"),
        source=TrainingPairSource.ARTIFACT,
    ),
    QueryPassagePair(
        query=NotBlankStr("Handle timeout"),
        positive_passage=NotBlankStr("Retry with backoff lesson"),
        source=TrainingPairSource.FAILURE_LESSON,
    ),
)

_PIPELINE = "synthorg.memory.embedding.fine_tune_pipeline"
_HELPERS = "synthorg.memory.embedding.fine_tune_run_helpers"


@contextlib.contextmanager
def _mock_stages_2_to_5() -> Iterator[None]:
    """Mock stages 2-5, leaving stage 1 (data generation) real."""

    async def _mine(**kwargs: object) -> Path:
        return Path("training_triples.jsonl")

    async def _train(**kwargs: object) -> Path:
        return Path("checkpoint")

    async def _eval(**kwargs: object) -> EvalMetrics:
        return EvalMetrics(
            ndcg_at_10=0.6,
            recall_at_10=0.7,
            base_ndcg_at_10=0.5,
            base_recall_at_10=0.6,
        )

    async def _deploy(**kwargs: object) -> str | None:
        return None

    with (
        patch(f"{_PIPELINE}.mine_hard_negatives", side_effect=_mine),
        patch(f"{_PIPELINE}.contrastive_fine_tune", side_effect=_train),
        patch(f"{_PIPELINE}.evaluate_checkpoint", side_effect=_eval),
        patch(f"{_PIPELINE}.deploy_checkpoint", side_effect=_deploy),
    ):
        yield


@pytest.mark.unit
class TestTrainingDataSourceSelection:
    """Stage 1 dispatches to the configured training-data source."""

    async def test_trajectory_mode_harvests_from_the_source(
        self,
        run_repo: SQLiteFineTuneRunRepository,
        cp_repo: SQLiteFineTuneCheckpointRepository,
    ) -> None:
        """Trajectory mode writes the harvested pairs and skips the scan."""
        source = _FakeTrainingDataSource(_HARVESTED_PAIRS)
        orchestrator = FineTuneOrchestrator(
            run_repo=run_repo,
            checkpoint_repo=cp_repo,
            training_data_source=source,
        )
        written: list[list[dict[str, str]]] = []

        async def _spy_writer(
            records: list[dict[str, str]],
            output_dir: str,
            *,
            validation_split: float,
        ) -> tuple[Path, Path]:
            written.append(records)
            return Path("training.jsonl"), Path("validation.jsonl")

        req = FineTuneRequest(data_source=FineTuneDataSourceType.TRAJECTORY)
        with (
            _mock_stages_2_to_5(),
            patch(f"{_HELPERS}.split_and_write_pairs", side_effect=_spy_writer),
            patch(f"{_HELPERS}.generate_training_data") as scan,
        ):
            await orchestrator.start(req)
            if orchestrator._current_task is not None:
                await orchestrator._current_task

        assert source.collect_calls == 1
        scan.assert_not_called()
        assert written == [
            [
                {
                    "query": "Build checkout",
                    "positive_passage": "Resilient checkout deliverable",
                },
                {
                    "query": "Handle timeout",
                    "positive_passage": "Retry with backoff lesson",
                },
            ]
        ]

    async def test_directory_mode_uses_the_document_scan(
        self,
        run_repo: SQLiteFineTuneRunRepository,
        cp_repo: SQLiteFineTuneCheckpointRepository,
        tmp_path: Path,
    ) -> None:
        """Directory mode (the default) still calls the document scan."""
        orchestrator = FineTuneOrchestrator(
            run_repo=run_repo,
            checkpoint_repo=cp_repo,
        )

        async def _scan(**kwargs: object) -> tuple[Path, Path]:
            return Path("training.jsonl"), Path("validation.jsonl")

        req = _request(tmp_path)
        assert req.data_source is FineTuneDataSourceType.DIRECTORY
        with (
            _mock_stages_2_to_5(),
            patch(f"{_HELPERS}.generate_training_data", side_effect=_scan) as scan,
            patch(f"{_HELPERS}.split_and_write_pairs") as writer,
        ):
            await orchestrator.start(req)
            if orchestrator._current_task is not None:
                await orchestrator._current_task

        scan.assert_called_once()
        writer.assert_not_called()

    async def test_trajectory_mode_without_source_fails_the_run(
        self,
        orchestrator: FineTuneOrchestrator,
        run_repo: SQLiteFineTuneRunRepository,
    ) -> None:
        """Selecting trajectory mode with no wired source fails the run."""
        req = FineTuneRequest(data_source=FineTuneDataSourceType.TRAJECTORY)
        with _mock_stages_2_to_5():
            run = await orchestrator.start(req)
            if orchestrator._current_task is not None:
                await orchestrator._current_task

        fetched = await run_repo.get(str(run.id))
        assert fetched is not None
        assert fetched.stage == FineTuneStage.FAILED


# -- Helpers ----------------------------------------------------------


@contextlib.contextmanager
def _mock_all_stages(
    *,
    block: bool = False,
    eval_metrics: EvalMetrics | None = None,
    deploy_calls: list[str] | None = None,
) -> Iterator[None]:
    """Mock all pipeline stage functions.

    If block=True, generate_training_data blocks until cancelled.
    ``eval_metrics`` overrides the evaluation-stage A/B result (defaults to a
    clear win); ``deploy_calls`` records each checkpoint path passed to the
    deploy mechanism so the promotion gate can be observed.
    """

    async def _gen_data(**kwargs: object) -> tuple[Path, Path]:
        if block:
            token = kwargs.get("cancellation")
            if isinstance(token, CancellationToken):
                # Block in a thread until cancelled. ``token.wait``
                # rides on a ``threading.Event`` so cancel wakes us
                # immediately without the 0.01s polling loop the
                # original test relied on.
                await asyncio.to_thread(token.wait)
                token.check()
            else:
                await asyncio.Event().wait()
        # Filesystem-free: the request carries synthetic POSIX paths
        # (``/test/out``) that resolve to an unwritable drive root on a
        # Linux CI runner. Every downstream stage is mocked and none
        # reads these files, so hand back path handles without touching
        # the disk (matches ``_mock_stages_2_to_5``).
        return Path("training.jsonl"), Path("validation.jsonl")

    async def _mine(**kwargs: object) -> Path:
        return Path("training_triples.jsonl")

    async def _train(**kwargs: object) -> Path:
        return Path("checkpoint")

    async def _eval(**kwargs: object) -> EvalMetrics:
        if eval_metrics is not None:
            return eval_metrics
        return EvalMetrics(
            ndcg_at_10=0.6,
            recall_at_10=0.7,
            base_ndcg_at_10=0.5,
            base_recall_at_10=0.6,
        )

    async def _deploy(**kwargs: object) -> str | None:
        if deploy_calls is not None:
            deploy_calls.append(str(kwargs.get("checkpoint_path", "")))
        return '{"embedder_model": "test-model"}'

    with (
        patch(f"{_HELPERS}.generate_training_data", side_effect=_gen_data),
        patch(f"{_PIPELINE}.mine_hard_negatives", side_effect=_mine),
        patch(f"{_PIPELINE}.contrastive_fine_tune", side_effect=_train),
        patch(f"{_PIPELINE}.evaluate_checkpoint", side_effect=_eval),
        patch(f"{_PIPELINE}.deploy_checkpoint", side_effect=_deploy),
    ):
        yield
