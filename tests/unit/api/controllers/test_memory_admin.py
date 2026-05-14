"""Tests for MemoryAdminController endpoints."""

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from synthorg.api.controllers.memory import (
    _BATCH_SIZE_BY_VRAM_GB,
    ActiveEmbedderResponse,
    MemoryAdminController,
    _recommend_batch_size,
)
from synthorg.memory.embedding.fine_tune import FineTuneStage
from synthorg.memory.embedding.fine_tune_models import (
    FineTuneRequest,
    FineTuneStatus,
)
from synthorg.settings.definitions.memory import FINE_TUNE_DEFAULT_BATCH_SIZE


@pytest.mark.unit
class TestFineTuneRequest:
    def test_valid(self) -> None:
        req = FineTuneRequest(source_dir="/data/docs")
        assert req.source_dir == "/data/docs"
        assert req.base_model is None
        assert req.output_dir is None

    def test_rejects_blank_source_dir(self) -> None:
        with pytest.raises(ValidationError, match="source_dir"):
            FineTuneRequest(source_dir="   ")

    def test_full_request(self) -> None:
        req = FineTuneRequest(
            source_dir="/data/docs",
            base_model="test-model",
            output_dir="/output",
        )
        assert req.base_model == "test-model"

    def test_rejects_traversal_in_source_dir(self) -> None:
        with pytest.raises(ValidationError, match="traversal"):
            FineTuneRequest(source_dir="/data/../etc")

    def test_rejects_windows_path_in_source_dir(self) -> None:
        with pytest.raises(ValidationError, match="POSIX"):
            FineTuneRequest(source_dir="C:\\data\\docs")

    def test_rejects_traversal_in_output_dir(self) -> None:
        with pytest.raises(ValidationError, match="traversal"):
            FineTuneRequest(source_dir="/data/docs", output_dir="/out/../secret")


@pytest.mark.unit
class TestFineTuneStatus:
    def test_defaults(self) -> None:
        status = FineTuneStatus()
        assert status.stage == FineTuneStage.IDLE
        assert status.progress is None
        assert status.error is None

    def test_valid_progress(self) -> None:
        status = FineTuneStatus(
            stage=FineTuneStage.TRAINING,
            progress=0.5,
        )
        assert status.progress == 0.5

    def test_rejects_progress_above_one(self) -> None:
        with pytest.raises(ValidationError):
            FineTuneStatus(progress=1.5)

    def test_rejects_negative_progress(self) -> None:
        with pytest.raises(ValidationError):
            FineTuneStatus(progress=-0.1)

    def test_rejects_nan_progress(self) -> None:
        with pytest.raises(ValidationError):
            FineTuneStatus(progress=float("nan"))

    def test_rejects_inf_progress(self) -> None:
        with pytest.raises(ValidationError):
            FineTuneStatus(progress=float("inf"))

    def test_with_error(self) -> None:
        status = FineTuneStatus(
            stage=FineTuneStage.FAILED,
            error="pipeline crashed",
        )
        assert status.error == "pipeline crashed"

    def test_rejects_idle_with_progress(self) -> None:
        with pytest.raises(ValidationError, match="IDLE"):
            FineTuneStatus(stage=FineTuneStage.IDLE, progress=0.5)

    def test_rejects_idle_with_error(self) -> None:
        with pytest.raises(ValidationError, match="IDLE"):
            FineTuneStatus(stage=FineTuneStage.IDLE, error="oops")

    def test_rejects_failed_without_error(self) -> None:
        with pytest.raises(ValidationError, match="FAILED"):
            FineTuneStatus(stage=FineTuneStage.FAILED)

    def test_rejects_active_with_error(self) -> None:
        with pytest.raises(ValidationError, match="active"):
            FineTuneStatus(
                stage=FineTuneStage.TRAINING,
                progress=0.5,
                error="should not be here",
            )

    def test_rejects_blank_error(self) -> None:
        with pytest.raises(ValidationError):
            FineTuneStatus(stage=FineTuneStage.FAILED, error="   ")


@pytest.mark.unit
class TestActiveEmbedderResponse:
    def test_defaults(self) -> None:
        resp = ActiveEmbedderResponse()
        assert resp.provider is None
        assert resp.model is None
        assert resp.dims is None

    def test_with_values(self) -> None:
        resp = ActiveEmbedderResponse(
            provider="test-provider",
            model="test-model",
            dims=768,
        )
        assert resp.provider == "test-provider"
        assert resp.dims == 768


@pytest.mark.unit
class TestMemoryAdminControllerExists:
    """Verify the controller is correctly defined."""

    def test_path(self) -> None:
        assert MemoryAdminController.path == "/admin/memory"

    def test_tags(self) -> None:
        assert "admin" in MemoryAdminController.tags
        assert "memory" in MemoryAdminController.tags


@pytest.mark.unit
class TestRecommendBatchSize:
    """Per-tier coverage for the VRAM -> batch-size lookup."""

    def test_fallback_on_missing_torch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing torch returns None, never raises."""
        import builtins

        real_import = builtins.__import__

        def _fake_import(
            name: str,
            *args: object,
            **kwargs: object,
        ) -> object:
            if name == "torch":
                msg = "no torch"
                raise ImportError(msg)
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        assert _recommend_batch_size() is None

    def test_cpu_only_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No CUDA -> default CPU batch size."""
        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = False
        monkeypatch.setitem(__import__("sys").modules, "torch", fake_torch)
        assert _recommend_batch_size() == FINE_TUNE_DEFAULT_BATCH_SIZE

    @pytest.mark.parametrize(
        ("vram_gb", "expected"),
        [
            pytest.param(80, 128, id="datacenter_gpu"),
            pytest.param(40, 128, id="40gb_boundary"),
            pytest.param(24, 64, id="24gb_consumer"),
            pytest.param(16, 64, id="16gb_boundary"),
            pytest.param(12, 32, id="12gb_mid"),
            pytest.param(8, 32, id="8gb_boundary"),
            pytest.param(4, FINE_TUNE_DEFAULT_BATCH_SIZE, id="sub_8gb_fallback"),
        ],
    )
    def test_vram_tier_returns_expected_batch_size(
        self,
        monkeypatch: pytest.MonkeyPatch,
        vram_gb: int,
        expected: int,
    ) -> None:
        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = True
        props = MagicMock()
        props.total_memory = vram_gb * (1024**3)
        fake_torch.cuda.get_device_properties.return_value = props
        monkeypatch.setitem(__import__("sys").modules, "torch", fake_torch)
        assert _recommend_batch_size() == expected

    def test_vram_table_is_descending(self) -> None:
        """Invariant: VRAM thresholds must be in strictly descending order."""
        thresholds = [gb for gb, _batch in _BATCH_SIZE_BY_VRAM_GB]
        assert thresholds == sorted(thresholds, reverse=True)
        assert len(thresholds) == len(set(thresholds))

    def test_unexpected_exception_is_logged_and_returns_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Unexpected errors in torch probing log a WARNING and return None.

        Guards the generic ``except Exception`` branch that reports via
        :data:`MEMORY_FINE_TUNE_BATCH_SIZE_RECOMMENDATION_FAILED`.
        """
        from synthorg.api.controllers import memory as memory_module
        from synthorg.observability.events.memory import (
            MEMORY_FINE_TUNE_BATCH_SIZE_RECOMMENDATION_FAILED,
        )

        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = True
        fake_torch.cuda.get_device_properties.side_effect = RuntimeError(
            "CUDA driver unavailable",
        )
        monkeypatch.setitem(__import__("sys").modules, "torch", fake_torch)

        warning_mock = MagicMock()
        # Direct setattr + try/finally delattr -- ``memory_module.logger``
        # is a ``BoundLoggerLazyProxy`` whose ``warning`` attribute is
        # served via ``__getattr__`` and is NOT in the instance
        # ``__dict__``. ``monkeypatch.setattr`` would snapshot
        # ``getattr(proxy, "warning")`` (a bound method on a current
        # ``BoundLogger``) and restore that snapshot at teardown,
        # permanently caching it into ``__dict__`` and shadowing
        # ``__getattr__``; later ``capture_logs()`` calls cannot
        # then reach the cached method's stale processor list.
        proxy = memory_module.logger
        proxy.warning = warning_mock  # type: ignore[method-assign]
        try:
            result = _recommend_batch_size()

            assert result is None
            warning_mock.assert_called_once()
            args, kwargs = warning_mock.call_args
            assert args[0] == MEMORY_FINE_TUNE_BATCH_SIZE_RECOMMENDATION_FAILED
            assert kwargs.get("error_type") == "RuntimeError"
            assert "CUDA driver unavailable" in kwargs.get("error", "")
            # ``exc_info`` is intentionally NOT set: passing the
            # exception chain to structlog appends the full traceback,
            # which bypasses the ``safe_error_description`` scrub and
            # can leak attacker-uncontrollable but operator-sensitive
            # detail (filesystem paths, fine-tune backend metadata,
            # CUDA driver versions). The assertion locks that exclusion
            # in place so a future refactor can't quietly add the
            # traceback.
            assert "exc_info" not in kwargs
        finally:
            from contextlib import suppress

            with suppress(AttributeError):
                del proxy.warning


@pytest.mark.unit
class TestDeleteMemoryEntryEndpoint:
    """Direct-method coverage for ``MemoryAdminController.delete_memory_entry``."""

    async def test_returns_ok_when_backend_deletes_entry(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from litestar.datastructures import State

        from synthorg.api.controllers import memory as memory_module
        from synthorg.api.controllers.memory import MemoryAdminController

        # Stub MemoryService to avoid the full _build_memory_service path.
        async def _delete_stub(agent_id: str, memory_id: str) -> bool:
            del agent_id, memory_id
            return False

        fake_service = SimpleNamespace(
            delete_memory_entry=AsyncMock(spec=_delete_stub, return_value=True),
        )

        def _fake_build(
            _app_state: object,
            *,
            require_fine_tune: bool = True,
        ) -> SimpleNamespace:
            return fake_service

        controller = MemoryAdminController(owner=None)  # type: ignore[arg-type]
        original_build = memory_module._build_memory_service
        memory_module._build_memory_service = _fake_build  # type: ignore[assignment]
        try:
            response = await controller.delete_memory_entry.fn(
                controller,
                state=State({"app_state": SimpleNamespace()}),
                agent_id="agent-1",
                memory_id="mem-1",
            )
        finally:
            memory_module._build_memory_service = original_build

        assert response.data is None
        fake_service.delete_memory_entry.assert_awaited_once_with(
            "agent-1",
            "mem-1",
        )

    async def test_raises_404_when_backend_returns_false(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from litestar.datastructures import State

        from synthorg.api.controllers import memory as memory_module
        from synthorg.api.controllers.memory import MemoryAdminController
        from synthorg.core.domain_errors import NotFoundError

        async def _delete_stub(agent_id: str, memory_id: str) -> bool:
            del agent_id, memory_id
            return False

        fake_service = SimpleNamespace(
            delete_memory_entry=AsyncMock(spec=_delete_stub, return_value=False),
        )

        def _fake_build(
            _app_state: object,
            *,
            require_fine_tune: bool = True,
        ) -> SimpleNamespace:
            return fake_service

        controller = MemoryAdminController(owner=None)  # type: ignore[arg-type]
        original_build = memory_module._build_memory_service
        memory_module._build_memory_service = _fake_build  # type: ignore[assignment]
        try:
            with pytest.raises(NotFoundError):
                await controller.delete_memory_entry.fn(
                    controller,
                    state=State({"app_state": SimpleNamespace()}),
                    agent_id="agent-1",
                    memory_id="missing",
                )
            fake_service.delete_memory_entry.assert_awaited_once_with(
                "agent-1",
                "missing",
            )
        finally:
            memory_module._build_memory_service = original_build

    async def test_raises_501_when_backend_unsupported(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from litestar.datastructures import State

        from synthorg.api.controllers import memory as memory_module
        from synthorg.api.controllers.memory import MemoryAdminController
        from synthorg.core.domain_errors import FeatureNotImplementedError
        from synthorg.memory.fine_tune_plan import MemoryBackendUnsupportedError

        async def _delete_stub(agent_id: str, memory_id: str) -> bool:
            del agent_id, memory_id
            return False

        fake_service = SimpleNamespace(
            delete_memory_entry=AsyncMock(
                spec=_delete_stub,
                side_effect=MemoryBackendUnsupportedError("no memory backend wired"),
            ),
        )

        def _fake_build(
            _app_state: object,
            *,
            require_fine_tune: bool = True,
        ) -> SimpleNamespace:
            return fake_service

        controller = MemoryAdminController(owner=None)  # type: ignore[arg-type]
        original_build = memory_module._build_memory_service
        memory_module._build_memory_service = _fake_build  # type: ignore[assignment]
        try:
            with pytest.raises(FeatureNotImplementedError) as exc_info:
                await controller.delete_memory_entry.fn(
                    controller,
                    state=State({"app_state": SimpleNamespace()}),
                    agent_id="agent-1",
                    memory_id="mem-1",
                )
            fake_service.delete_memory_entry.assert_awaited_once_with(
                "agent-1",
                "mem-1",
            )
        finally:
            memory_module._build_memory_service = original_build
        assert exc_info.value.status_code == 501


@pytest.mark.unit
class TestResolveFineTuneThresholds:
    """Verify the runtime resolver consults SettingsService overrides."""

    async def test_falls_back_to_imported_defaults_when_service_missing(
        self,
    ) -> None:
        """A ``None`` service path returns the imported defaults verbatim."""
        from synthorg.api.controllers.memory import _resolve_fine_tune_thresholds
        from synthorg.settings.definitions.memory import (
            FINE_TUNE_DEFAULT_BATCH_SIZE,
            FINE_TUNE_MIN_DOCS_RECOMMENDED,
            FINE_TUNE_MIN_DOCS_REQUIRED,
        )

        thresholds = await _resolve_fine_tune_thresholds(None)
        assert thresholds.default_batch_size == FINE_TUNE_DEFAULT_BATCH_SIZE
        assert thresholds.min_docs_required == FINE_TUNE_MIN_DOCS_REQUIRED
        assert thresholds.min_docs_recommended == FINE_TUNE_MIN_DOCS_RECOMMENDED

    async def test_overrides_from_settings_service_take_effect(self) -> None:
        """SettingsService values override the imported defaults."""
        from unittest.mock import AsyncMock

        from synthorg.api.controllers.memory import _resolve_fine_tune_thresholds
        from synthorg.core.types import NotBlankStr
        from synthorg.settings.enums import SettingNamespace, SettingSource
        from synthorg.settings.models import SettingValue
        from synthorg.settings.service import SettingsService

        async def _fake_get(_namespace: str, key: str) -> SettingValue:
            override_value = {
                "fine_tune_default_batch_size": "256",
                "fine_tune_min_docs_required": "25",
                "fine_tune_min_docs_recommended": "75",
            }[key]
            return SettingValue(
                namespace=SettingNamespace.MEMORY,
                key=NotBlankStr(key),
                value=override_value,
                source=SettingSource.DATABASE,
            )

        service = AsyncMock(spec=SettingsService)
        service.get.side_effect = _fake_get

        thresholds = await _resolve_fine_tune_thresholds(service)
        assert thresholds.default_batch_size == 256
        assert thresholds.min_docs_required == 25
        assert thresholds.min_docs_recommended == 75

    async def test_unparseable_value_falls_back_to_default(self) -> None:
        """A non-integer setting value drops to the imported fallback."""
        from unittest.mock import AsyncMock

        from synthorg.api.controllers.memory import _resolve_fine_tune_thresholds
        from synthorg.core.types import NotBlankStr
        from synthorg.settings.definitions.memory import (
            FINE_TUNE_DEFAULT_BATCH_SIZE,
            FINE_TUNE_MIN_DOCS_RECOMMENDED,
            FINE_TUNE_MIN_DOCS_REQUIRED,
        )
        from synthorg.settings.enums import SettingNamespace, SettingSource
        from synthorg.settings.models import SettingValue
        from synthorg.settings.service import SettingsService

        async def _fake_get(_namespace: str, key: str) -> SettingValue:
            return SettingValue(
                namespace=SettingNamespace.MEMORY,
                key=NotBlankStr(key),
                value="not-an-int",
                source=SettingSource.DATABASE,
            )

        service = AsyncMock(spec=SettingsService)
        service.get.side_effect = _fake_get

        thresholds = await _resolve_fine_tune_thresholds(service)
        assert thresholds.default_batch_size == FINE_TUNE_DEFAULT_BATCH_SIZE
        assert thresholds.min_docs_required == FINE_TUNE_MIN_DOCS_REQUIRED
        assert thresholds.min_docs_recommended == FINE_TUNE_MIN_DOCS_RECOMMENDED

    async def test_missing_setting_falls_back_to_default(self) -> None:
        """SettingNotFoundError on lookup drops to the imported fallback."""
        from unittest.mock import AsyncMock

        from synthorg.api.controllers.memory import _resolve_fine_tune_thresholds
        from synthorg.settings.definitions.memory import (
            FINE_TUNE_DEFAULT_BATCH_SIZE,
            FINE_TUNE_MIN_DOCS_RECOMMENDED,
            FINE_TUNE_MIN_DOCS_REQUIRED,
        )
        from synthorg.settings.errors import SettingNotFoundError
        from synthorg.settings.service import SettingsService

        service = AsyncMock(spec=SettingsService)
        service.get.side_effect = SettingNotFoundError("missing")

        thresholds = await _resolve_fine_tune_thresholds(service)
        assert thresholds.default_batch_size == FINE_TUNE_DEFAULT_BATCH_SIZE
        assert thresholds.min_docs_required == FINE_TUNE_MIN_DOCS_REQUIRED
        assert thresholds.min_docs_recommended == FINE_TUNE_MIN_DOCS_RECOMMENDED


@pytest.mark.unit
class TestCheckDocumentsBoundaries:
    """Boundary cases for ``_check_documents`` against the warn / fail thresholds."""

    def test_count_at_recommended_threshold_warns(self, tmp_path: object) -> None:
        """A corpus exactly at the recommended threshold must warn, not pass.

        Setting description is "warn band for corpora at or below this
        size", so the boundary is inclusive.
        """
        from pathlib import Path

        from synthorg.api.controllers.memory import _check_documents

        src = Path(str(tmp_path))
        for i in range(50):
            (src / f"doc-{i:02d}.md").write_text("x")
        check = _check_documents(
            str(src),
            min_required=10,
            min_recommended=50,
        )
        assert check.status == "warn"

    def test_count_above_recommended_passes(self, tmp_path: object) -> None:
        """One document above the recommended threshold flips to pass."""
        from pathlib import Path

        from synthorg.api.controllers.memory import _check_documents

        src = Path(str(tmp_path))
        for i in range(51):
            (src / f"doc-{i:02d}.md").write_text("x")
        check = _check_documents(
            str(src),
            min_required=10,
            min_recommended=50,
        )
        assert check.status == "pass"

    def test_count_below_required_fails(self, tmp_path: object) -> None:
        """A corpus below the hard floor fails."""
        from pathlib import Path

        from synthorg.api.controllers.memory import _check_documents

        src = Path(str(tmp_path))
        for i in range(5):
            (src / f"doc-{i:02d}.md").write_text("x")
        check = _check_documents(
            str(src),
            min_required=10,
            min_recommended=50,
        )
        assert check.status == "fail"

    def test_count_at_required_threshold_does_not_fail(
        self,
        tmp_path: object,
    ) -> None:
        """A corpus exactly at ``min_required`` clears the hard floor.

        The fail branch uses a strict ``<`` so a count equal to
        ``min_required`` must NOT fail; it still warns because it
        sits below ``min_recommended``.
        """
        from pathlib import Path

        from synthorg.api.controllers.memory import _check_documents

        src = Path(str(tmp_path))
        for i in range(10):
            (src / f"doc-{i:02d}.md").write_text("x")
        check = _check_documents(
            str(src),
            min_required=10,
            min_recommended=50,
        )
        assert check.status == "warn"


@pytest.mark.unit
class TestListCheckpointsEndpoint:
    """Direct-method coverage for ``MemoryAdminController.list_checkpoints``."""

    async def test_no_cursor_starts_at_offset_zero(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from litestar.datastructures import State

        from synthorg.api.controllers import memory as memory_module
        from synthorg.api.controllers.memory import MemoryAdminController
        from synthorg.api.cursor import CursorSecret

        async def _list_stub(
            limit: int,
            offset: int,
        ) -> tuple[tuple[object, ...], int]:
            del limit, offset
            return ((), 0)

        list_mock = AsyncMock(spec=_list_stub, return_value=((), 0))
        fake_service = SimpleNamespace(list_checkpoints=list_mock)

        def _fake_build(
            _app_state: object,
            *,
            require_fine_tune: bool = True,
        ) -> SimpleNamespace:
            del require_fine_tune
            return fake_service

        controller = MemoryAdminController(owner=None)  # type: ignore[arg-type]
        original_build = memory_module._build_memory_service
        memory_module._build_memory_service = _fake_build  # type: ignore[assignment]
        try:
            app_state = SimpleNamespace(cursor_secret=CursorSecret.ephemeral())
            response = await controller.list_checkpoints.fn(
                controller,
                state=State({"app_state": app_state}),
                cursor=None,
                limit=50,
            )
        finally:
            memory_module._build_memory_service = original_build

        assert response.data == ()
        list_mock.assert_awaited_once_with(limit=50, offset=0)

    async def test_with_cursor_decodes_to_offset(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from litestar.datastructures import State

        from synthorg.api.controllers import memory as memory_module
        from synthorg.api.controllers.memory import MemoryAdminController
        from synthorg.api.cursor import CursorSecret, encode_cursor

        async def _list_stub(
            limit: int,
            offset: int,
        ) -> tuple[tuple[object, ...], int]:
            del limit, offset
            return ((), 25)

        list_mock = AsyncMock(spec=_list_stub, return_value=((), 25))
        fake_service = SimpleNamespace(list_checkpoints=list_mock)

        def _fake_build(
            _app_state: object,
            *,
            require_fine_tune: bool = True,
        ) -> SimpleNamespace:
            del require_fine_tune
            return fake_service

        secret = CursorSecret.ephemeral()
        cursor = encode_cursor(10, secret=secret)
        controller = MemoryAdminController(owner=None)  # type: ignore[arg-type]
        original_build = memory_module._build_memory_service
        memory_module._build_memory_service = _fake_build  # type: ignore[assignment]
        try:
            await controller.list_checkpoints.fn(
                controller,
                state=State({"app_state": SimpleNamespace(cursor_secret=secret)}),
                cursor=cursor,
                limit=50,
            )
        finally:
            memory_module._build_memory_service = original_build

        list_mock.assert_awaited_once_with(limit=50, offset=10)

    async def test_tampered_cursor_raises(self) -> None:
        from types import SimpleNamespace

        from litestar.datastructures import State

        from synthorg.api.controllers.memory import MemoryAdminController
        from synthorg.api.cursor import CursorSecret, InvalidCursorError

        controller = MemoryAdminController(owner=None)  # type: ignore[arg-type]
        app_state = SimpleNamespace(cursor_secret=CursorSecret.ephemeral())
        with pytest.raises(InvalidCursorError):
            await controller.list_checkpoints.fn(
                controller,
                state=State({"app_state": app_state}),
                cursor="not-a-real-cursor",
                limit=50,
            )


@pytest.mark.unit
class TestListRunsEndpoint:
    """Direct-method coverage for ``MemoryAdminController.list_runs``."""

    async def test_no_cursor_starts_at_offset_zero(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from litestar.datastructures import State

        from synthorg.api.controllers import memory as memory_module
        from synthorg.api.controllers.memory import MemoryAdminController
        from synthorg.api.cursor import CursorSecret

        async def _list_stub(
            limit: int,
            offset: int,
        ) -> tuple[tuple[object, ...], int]:
            del limit, offset
            return ((), 0)

        list_mock = AsyncMock(spec=_list_stub, return_value=((), 0))
        fake_service = SimpleNamespace(list_runs=list_mock)

        def _fake_build(
            _app_state: object,
            *,
            require_fine_tune: bool = True,
        ) -> SimpleNamespace:
            del require_fine_tune
            return fake_service

        controller = MemoryAdminController(owner=None)  # type: ignore[arg-type]
        original_build = memory_module._build_memory_service
        memory_module._build_memory_service = _fake_build  # type: ignore[assignment]
        try:
            app_state = SimpleNamespace(cursor_secret=CursorSecret.ephemeral())
            response = await controller.list_runs.fn(
                controller,
                state=State({"app_state": app_state}),
                cursor=None,
                limit=50,
            )
        finally:
            memory_module._build_memory_service = original_build

        assert response.data == ()
        list_mock.assert_awaited_once_with(limit=50, offset=0)

    async def test_with_cursor_decodes_to_offset(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from litestar.datastructures import State

        from synthorg.api.controllers import memory as memory_module
        from synthorg.api.controllers.memory import MemoryAdminController
        from synthorg.api.cursor import CursorSecret, encode_cursor

        async def _list_stub(
            limit: int,
            offset: int,
        ) -> tuple[tuple[object, ...], int]:
            del limit, offset
            return ((), 25)

        list_mock = AsyncMock(spec=_list_stub, return_value=((), 25))
        fake_service = SimpleNamespace(list_runs=list_mock)

        def _fake_build(
            _app_state: object,
            *,
            require_fine_tune: bool = True,
        ) -> SimpleNamespace:
            del require_fine_tune
            return fake_service

        secret = CursorSecret.ephemeral()
        cursor = encode_cursor(15, secret=secret)
        controller = MemoryAdminController(owner=None)  # type: ignore[arg-type]
        original_build = memory_module._build_memory_service
        memory_module._build_memory_service = _fake_build  # type: ignore[assignment]
        try:
            await controller.list_runs.fn(
                controller,
                state=State({"app_state": SimpleNamespace(cursor_secret=secret)}),
                cursor=cursor,
                limit=50,
            )
        finally:
            memory_module._build_memory_service = original_build

        list_mock.assert_awaited_once_with(limit=50, offset=15)

    async def test_tampered_cursor_raises(self) -> None:
        from types import SimpleNamespace

        from litestar.datastructures import State

        from synthorg.api.controllers.memory import MemoryAdminController
        from synthorg.api.cursor import CursorSecret, InvalidCursorError

        controller = MemoryAdminController(owner=None)  # type: ignore[arg-type]
        app_state = SimpleNamespace(cursor_secret=CursorSecret.ephemeral())
        with pytest.raises(InvalidCursorError):
            await controller.list_runs.fn(
                controller,
                state=State({"app_state": app_state}),
                cursor="not-a-real-cursor",
                limit=50,
            )
