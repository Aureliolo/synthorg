"""Tests for MemoryAdminController endpoints."""

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from synthorg.api.controllers.memory import (
    _BATCH_SIZE_BY_VRAM_GB,
    _DEFAULT_BATCH_SIZE,
    ActiveEmbedderResponse,
    MemoryAdminController,
    _recommend_batch_size,
)
from synthorg.memory.embedding.fine_tune import FineTuneStage
from synthorg.memory.embedding.fine_tune_models import (
    FineTuneRequest,
    FineTuneStatus,
)


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
        assert _recommend_batch_size() == _DEFAULT_BATCH_SIZE

    @pytest.mark.parametrize(
        ("vram_gb", "expected"),
        [
            pytest.param(80, 128, id="datacenter_gpu"),
            pytest.param(40, 128, id="40gb_boundary"),
            pytest.param(24, 64, id="24gb_consumer"),
            pytest.param(16, 64, id="16gb_boundary"),
            pytest.param(12, 32, id="12gb_mid"),
            pytest.param(8, 32, id="8gb_boundary"),
            pytest.param(4, _DEFAULT_BATCH_SIZE, id="sub_8gb_fallback"),
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
        monkeypatch.setattr(memory_module.logger, "warning", warning_mock)

        result = _recommend_batch_size()

        assert result is None
        warning_mock.assert_called_once()
        args, kwargs = warning_mock.call_args
        assert args[0] == MEMORY_FINE_TUNE_BATCH_SIZE_RECOMMENDATION_FAILED
        assert kwargs.get("error_type") == "RuntimeError"
        assert "CUDA driver unavailable" in kwargs.get("error", "")
        assert kwargs.get("exc_info") is True


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
        fake_service = SimpleNamespace(delete_memory_entry=AsyncMock(return_value=True))

        def _fake_build(_app_state: object) -> SimpleNamespace:
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
        fake_service.delete_memory_entry.assert_awaited_once()

    async def test_raises_404_when_backend_returns_false(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from litestar.datastructures import State
        from litestar.exceptions import NotFoundException

        from synthorg.api.controllers import memory as memory_module
        from synthorg.api.controllers.memory import MemoryAdminController

        fake_service = SimpleNamespace(
            delete_memory_entry=AsyncMock(return_value=False),
        )

        def _fake_build(_app_state: object) -> SimpleNamespace:
            return fake_service

        controller = MemoryAdminController(owner=None)  # type: ignore[arg-type]
        original_build = memory_module._build_memory_service
        memory_module._build_memory_service = _fake_build  # type: ignore[assignment]
        try:
            with pytest.raises(NotFoundException):
                await controller.delete_memory_entry.fn(
                    controller,
                    state=State({"app_state": SimpleNamespace()}),
                    agent_id="agent-1",
                    memory_id="missing",
                )
        finally:
            memory_module._build_memory_service = original_build

    async def test_raises_501_when_backend_unsupported(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from litestar.datastructures import State
        from litestar.exceptions import ClientException

        from synthorg.api.controllers import memory as memory_module
        from synthorg.api.controllers.memory import MemoryAdminController
        from synthorg.memory.fine_tune_plan import BackendUnsupportedError

        fake_service = SimpleNamespace(
            delete_memory_entry=AsyncMock(
                side_effect=BackendUnsupportedError("no memory backend wired"),
            ),
        )

        def _fake_build(_app_state: object) -> SimpleNamespace:
            return fake_service

        controller = MemoryAdminController(owner=None)  # type: ignore[arg-type]
        original_build = memory_module._build_memory_service
        memory_module._build_memory_service = _fake_build  # type: ignore[assignment]
        try:
            with pytest.raises(ClientException) as exc_info:
                await controller.delete_memory_entry.fn(
                    controller,
                    state=State({"app_state": SimpleNamespace()}),
                    agent_id="agent-1",
                    memory_id="mem-1",
                )
        finally:
            memory_module._build_memory_service = original_build
        assert exc_info.value.status_code == 501
