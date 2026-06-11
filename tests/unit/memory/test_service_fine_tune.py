"""Unit tests for :class:`MemoryService` fine-tune lifecycle extensions."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.memory.embedding.fine_tune import FineTuneStage
from synthorg.memory.embedding.fine_tune_models import (
    CheckpointRecord,
    FineTuneDataSourceType,
    FineTuneRequest,
    FineTuneRun,
    FineTuneRunConfig,
    FineTuneStatus,
)
from synthorg.memory.fine_tune_plan import FineTunePlan, MemoryBackendUnsupportedError
from synthorg.memory.service import MemoryService
from synthorg.settings.enums import SettingNamespace, SettingSource
from synthorg.settings.errors import SettingNotFoundError
from synthorg.settings.models import SettingValue
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 4, 24, 12, 0, tzinfo=UTC)


def _run(
    run_id: str = "run-1",
    stage: FineTuneStage = FineTuneStage.GENERATING_DATA,
) -> FineTuneRun:
    return FineTuneRun(
        id=as_uuid(run_id),
        stage=stage,
        config=FineTuneRunConfig(
            source_dir=NotBlankStr("/data/org-docs"),
            base_model=NotBlankStr("all-MiniLM-L6-v2"),
            output_dir=NotBlankStr("/data/fine-tune"),
        ),
        started_at=_NOW,
        updated_at=_NOW,
    )


def _checkpoint(
    checkpoint_id: str = "ckpt-1",
    *,
    is_active: bool = False,
) -> CheckpointRecord:
    return CheckpointRecord(
        id=as_uuid(checkpoint_id),
        run_id=NotBlankStr(sid("run-1")),
        model_path=NotBlankStr("local/ckpt-1"),
        base_model=NotBlankStr("all-MiniLM-L6-v2"),
        doc_count=10,
        eval_metrics=None,
        size_bytes=1024,
        created_at=_NOW,
        is_active=is_active,
    )


class _FakeRunRepo:
    def __init__(self, runs: list[FineTuneRun] | None = None) -> None:
        self._runs = list(runs or [])

    async def save(self, entity: FineTuneRun) -> None:
        self._runs.append(entity)

    async def get(self, entity_id: str) -> FineTuneRun | None:
        for r in self._runs:
            if str(r.id) == entity_id:
                return r
        return None

    async def delete(self, entity_id: str) -> bool:
        before = len(self._runs)
        self._runs = [r for r in self._runs if str(r.id) != entity_id]
        return len(self._runs) < before

    async def get_active_run(self) -> FineTuneRun | None:
        return None

    async def list_items(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[FineTuneRun, ...]:
        return tuple(self._runs[offset : offset + limit])

    async def list_items_page(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[tuple[FineTuneRun, ...], int]:
        values = tuple(self._runs)
        return values[offset : offset + limit], len(values)

    async def update_run(self, run: FineTuneRun) -> None:
        pass

    async def mark_interrupted(self) -> int:
        return 0


class _FakeCheckpointRepo:
    def __init__(
        self,
        checkpoints: list[CheckpointRecord] | None = None,
    ) -> None:
        self._rows = {str(c.id): c for c in checkpoints or []}

    async def save(self, entity: CheckpointRecord) -> None:
        self._rows[str(entity.id)] = entity

    async def get(self, entity_id: str) -> CheckpointRecord | None:
        return self._rows.get(entity_id)

    async def list_items(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[CheckpointRecord, ...]:
        values = tuple(self._rows.values())
        return values[offset : offset + limit]

    async def list_items_page(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[tuple[CheckpointRecord, ...], int]:
        values = tuple(self._rows.values())
        return values[offset : offset + limit], len(values)

    async def set_active(self, cid: str) -> None:
        self._rows[cid] = self._rows[cid].model_copy(update={"is_active": True})

    async def deactivate_all(self) -> None:
        for k, v in list(self._rows.items()):
            self._rows[k] = v.model_copy(update={"is_active": False})

    async def delete(self, entity_id: str) -> bool:
        return self._rows.pop(entity_id, None) is not None

    async def get_active_checkpoint(self) -> CheckpointRecord | None:
        for v in self._rows.values():
            if v.is_active:
                return v
        return None


class _FakeOrchestrator:
    def __init__(self) -> None:
        self.start_calls: list[FineTuneRequest] = []
        self.resume_calls: list[str] = []
        self.cancel_calls: int = 0
        self._status = FineTuneStatus()
        # ``current_run`` mirrors the real orchestrator's property so
        # ``MemoryService.cancel_fine_tune`` can capture the active run
        # id before cancel for the destructive-op audit event. Default
        # ``None`` keeps existing happy-path tests stable; tests that
        # care can assign a ``FineTuneRun`` before invoking cancel.
        self.current_run: FineTuneRun | None = None

    async def start(self, request: FineTuneRequest) -> FineTuneRun:
        self.start_calls.append(request)
        return _run()

    async def resume(self, run_id: str) -> FineTuneRun:
        self.resume_calls.append(run_id)
        return _run(run_id=run_id)

    async def cancel(self) -> None:
        self.cancel_calls += 1

    async def get_status(self) -> FineTuneStatus:
        return self._status


class _FakeSettings:
    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    async def get(self, namespace: str, key: str) -> SettingValue:
        value = self._store.get((namespace, key))
        if value is None:
            msg = f"{namespace}.{key} not set"
            raise SettingNotFoundError(msg)
        return SettingValue(
            namespace=SettingNamespace(namespace),
            key=NotBlankStr(key),
            value=value,
            source=SettingSource.DATABASE,
            updated_at=None,
        )

    async def set(self, namespace: str, key: str, value: str) -> None:
        self._store[(namespace, key)] = value

    async def delete(self, namespace: str, key: str) -> None:
        self._store.pop((namespace, key), None)


def _service(
    *,
    orchestrator: _FakeOrchestrator | None = None,
    checkpoints: list[CheckpointRecord] | None = None,
    runs: list[FineTuneRun] | None = None,
    settings: _FakeSettings | None = None,
) -> MemoryService:
    return MemoryService(
        checkpoint_repo=_FakeCheckpointRepo(checkpoints),
        run_repo=_FakeRunRepo(runs),
        settings_service=settings,
        orchestrator=orchestrator,
    )


class TestBackendUnsupported:
    """Every fine-tune method raises when orchestrator is None."""

    async def test_start_raises(self) -> None:
        service = _service()
        plan = FineTunePlan(source_dir=NotBlankStr("/data/org-docs"))

        with pytest.raises(MemoryBackendUnsupportedError):
            await service.start_fine_tune(plan)

    async def test_resume_raises(self) -> None:
        service = _service()

        with pytest.raises(MemoryBackendUnsupportedError):
            await service.resume_fine_tune(NotBlankStr("run-x"))

    async def test_status_raises(self) -> None:
        service = _service()

        with pytest.raises(MemoryBackendUnsupportedError):
            await service.get_fine_tune_status()

    async def test_cancel_raises(self) -> None:
        service = _service()

        with pytest.raises(MemoryBackendUnsupportedError):
            await service.cancel_fine_tune()

    async def test_preflight_raises(self) -> None:
        service = _service()

        with pytest.raises(MemoryBackendUnsupportedError):
            await service.run_preflight(
                FineTunePlan(source_dir=NotBlankStr("/data/org-docs")),
            )


class TestStartFineTune:
    async def test_delegates_to_orchestrator(self) -> None:
        orch = _FakeOrchestrator()
        service = _service(orchestrator=orch)
        plan = FineTunePlan(
            source_dir=NotBlankStr("/data/org-docs"),
            epochs=5,
        )

        run = await service.start_fine_tune(plan)

        assert run.id == as_uuid("run-1")
        assert len(orch.start_calls) == 1
        request = orch.start_calls[0]
        assert request.source_dir == "/data/org-docs"
        assert request.epochs == 5


class TestResumeFineTune:
    async def test_delegates_to_orchestrator(self) -> None:
        orch = _FakeOrchestrator()
        service = _service(orchestrator=orch)

        run = await service.resume_fine_tune(NotBlankStr("run-42"))

        assert run.id == as_uuid("run-42")
        assert orch.resume_calls == ["run-42"]


class TestGetFineTuneStatus:
    async def test_no_run_id_uses_orchestrator_current(self) -> None:
        orch = _FakeOrchestrator()
        service = _service(orchestrator=orch)

        status = await service.get_fine_tune_status()

        assert isinstance(status, FineTuneStatus)

    async def test_with_run_id_looks_up_persistence(self) -> None:
        run = _run(run_id="run-99", stage=FineTuneStage.GENERATING_DATA)
        service = _service(orchestrator=_FakeOrchestrator(), runs=[run])

        status = await service.get_fine_tune_status(sid("run-99"))

        assert status.run_id == sid("run-99")
        assert status.stage == FineTuneStage.GENERATING_DATA

    async def test_missing_run_id_raises(self) -> None:
        service = _service(orchestrator=_FakeOrchestrator())

        with pytest.raises(ValueError, match="not found"):
            await service.get_fine_tune_status(NotBlankStr("nonexistent"))


class TestCancelFineTune:
    async def test_delegates_to_orchestrator(self) -> None:
        orch = _FakeOrchestrator()
        service = _service(orchestrator=orch)

        await service.cancel_fine_tune()

        assert orch.cancel_calls == 1

    async def test_returns_active_run_id_captured_before_cancel(self) -> None:
        """``cancel_fine_tune`` returns the active run id captured pre-cancel.

        The event emission contract lives on the orchestrator
        (:meth:`FineTuneOrchestrator.cancel` already emits
        :data:`MEMORY_FINE_TUNE_CANCELLED`), so the service no longer
        double-emits it. Instead, the service's contract is to return
        the cancelled run id so MCP handlers can attribute the
        ``MCP_ADMIN_OP_EXECUTED`` audit event to the right run.
        """
        orch = _FakeOrchestrator()
        run = _run(run_id="run-42")
        orch.current_run = run
        service = _service(orchestrator=orch)

        result = await service.cancel_fine_tune()

        assert result == sid("run-42")
        assert orch.cancel_calls == 1

    async def test_returns_none_when_no_active_run(self) -> None:
        """Returns ``None`` when the orchestrator has no active run.

        This branch must NOT synthesise a false MCP audit event: the
        handler checks the returned id and omits ``target_id`` when
        there was nothing to cancel.
        """
        orch = _FakeOrchestrator()
        orch.current_run = None
        service = _service(orchestrator=orch)

        result = await service.cancel_fine_tune()

        assert result is None
        assert orch.cancel_calls == 1


class TestRunPreflight:
    """Preflight is platform-agnostic; we mock the filesystem calls.

    ``FineTunePlan`` rejects backslashes and drive letters on purpose
    (path-traversal guard), so we cannot use the OS tmpdir on
    Windows. Mocking ``Path.exists`` / ``Path.is_dir`` keeps the test
    portable.
    """

    async def test_missing_source_dir_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(Path, "exists", lambda self: False)
        service = _service(orchestrator=_FakeOrchestrator())
        plan = FineTunePlan(source_dir=NotBlankStr("/data/org-docs/missing"))

        result = await service.run_preflight(plan)

        assert not result.can_proceed
        assert any(c.status == "fail" for c in result.checks)

    async def test_existing_source_dir_passes(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(Path, "exists", lambda self: True)
        monkeypatch.setattr(Path, "is_dir", lambda self: True)
        monkeypatch.setattr(
            "synthorg.memory.fine_tune_admin_service.os.access",
            lambda _p, _m: True,
        )
        service = _service(orchestrator=_FakeOrchestrator())
        plan = FineTunePlan(source_dir=NotBlankStr("/data/org-docs"))

        result = await service.run_preflight(plan)

        assert result.can_proceed
        assert all(c.status != "fail" for c in result.checks)

    async def test_overrides_check_always_present(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(Path, "exists", lambda self: True)
        monkeypatch.setattr(Path, "is_dir", lambda self: True)
        monkeypatch.setattr(
            "synthorg.memory.fine_tune_admin_service.os.access",
            lambda _p, _m: True,
        )
        service = _service(orchestrator=_FakeOrchestrator())
        plan = FineTunePlan(
            source_dir=NotBlankStr("/data/org-docs"),
            epochs=3,
            learning_rate=1e-5,
        )

        result = await service.run_preflight(plan)

        override_checks = [c for c in result.checks if c.name == "override_bounds"]
        assert len(override_checks) == 1
        assert override_checks[0].status == "pass"

    async def test_unreadable_source_dir_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``os.access(R_OK)`` returning ``False`` fails the check.

        The preflight pass message promises readability, so a directory
        that exists and is a directory but that the runner cannot
        actually read must fail preflight rather than proceed to
        pipeline start.
        """
        monkeypatch.setattr(Path, "exists", lambda self: True)
        monkeypatch.setattr(Path, "is_dir", lambda self: True)
        monkeypatch.setattr(
            "synthorg.memory.fine_tune_admin_service.os.access",
            lambda _p, _m: False,
        )
        service = _service(orchestrator=_FakeOrchestrator())
        plan = FineTunePlan(source_dir=NotBlankStr("/data/org-docs"))

        result = await service.run_preflight(plan)

        assert not result.can_proceed
        source_checks = [c for c in result.checks if c.name == "source_dir_exists"]
        assert source_checks
        assert source_checks[0].status == "fail"
        assert "not readable" in source_checks[0].message

    async def test_trajectory_mode_skips_source_dir_check(self) -> None:
        """Trajectory mode has no ``source_dir`` to stat -- the check is skipped.

        Statting ``None`` would raise ``TypeError`` inside
        ``_check_source_dir_exists``; the guard must elide it so a
        trajectory preflight stays a clean, deterministic result.
        """
        service = _service(orchestrator=_FakeOrchestrator())
        plan = FineTunePlan(data_source=FineTuneDataSourceType.TRAJECTORY)

        result = await service.run_preflight(plan)

        source_checks = [c for c in result.checks if c.name == "source_dir_exists"]
        assert not source_checks
        override_checks = [c for c in result.checks if c.name == "override_bounds"]
        assert len(override_checks) == 1


class TestListRunsPagination:
    """``list_runs`` now returns ``(items, total)``."""

    async def test_returns_tuple_with_total(self) -> None:
        runs = [_run(run_id=f"r-{i}") for i in range(3)]
        service = _service(runs=runs)

        items, total = await service.list_runs(limit=50, offset=0)

        assert total == 3
        assert len(items) == 3


class TestGetActiveEmbedder:
    async def test_no_settings_returns_snapshot(self) -> None:
        checkpoint = _checkpoint(is_active=True)
        service = _service(
            checkpoints=[checkpoint],
            settings=None,
        )

        snap = await service.get_active_embedder()

        assert snap.read_from_settings is False
        assert snap.checkpoint_id == sid("ckpt-1")

    async def test_with_settings_reads_provider_and_model(self) -> None:
        settings = _FakeSettings()
        await settings.set("memory", "embedder_provider", "local")
        await settings.set("memory", "embedder_model", "local/ckpt-1")
        service = _service(
            checkpoints=[_checkpoint(is_active=True)],
            settings=settings,
        )

        snap = await service.get_active_embedder()

        assert snap.read_from_settings is True
        assert snap.provider == "local"
        assert snap.model == "local/ckpt-1"
        assert snap.checkpoint_id == sid("ckpt-1")

    async def test_no_active_checkpoint(self) -> None:
        service = _service(checkpoints=[])

        snap = await service.get_active_embedder()

        assert snap.checkpoint_id is None
