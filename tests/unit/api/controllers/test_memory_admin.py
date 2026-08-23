"""Tests for MemoryAdminController endpoints."""

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from synthorg.api.controllers.memory._preflight import (
    _BATCH_SIZE_BY_VRAM_GB,
    _recommend_batch_size,
)
from synthorg.api.controllers.memory.checkpoints import MemoryCheckpointsController
from synthorg.api.controllers.memory.embedder import (
    ActiveEmbedderResponse,
    MemoryEmbedderController,
)
from synthorg.api.controllers.memory.entries import MemoryEntriesController
from synthorg.api.controllers.memory.fine_tune import MemoryFineTuneController
from synthorg.memory.embedding.fine_tune import FineTuneStage
from synthorg.memory.embedding.fine_tune_models import (
    FineTuneRequest,
    FineTuneStatus,
)
from synthorg.memory.errors import FineTuneDependencyError
from synthorg.settings.definitions.memory_fine_tune import FINE_TUNE_DEFAULT_BATCH_SIZE
from tests._shared import make_app_state, module_double, torch_double


class _AllMemoryControllers(
    MemoryFineTuneController,
    MemoryCheckpointsController,
    MemoryEntriesController,
    MemoryEmbedderController,
):
    """Test-only composite exposing every memory-admin route handler.

    The decomposed sub-controllers each own a slice of the
    ``/admin/memory`` surface; this composite gives the direct-method
    tests one instance from which every handler resolves, mirroring the
    pre-decomposition single-controller shape.
    """


# Alias so the direct-method tests below can address the composite under a
# single controller name; the handlers they exercise via ``.fn`` do not use
# ``self``, so the composite stands in transparently.
MemoryAdminController = _AllMemoryControllers


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
        assert MemoryAdminController.tags is not None
        assert "admin" in MemoryAdminController.tags
        assert "memory" in MemoryAdminController.tags


@pytest.mark.unit
class TestRecommendBatchSize:
    """Per-tier coverage for the VRAM -> batch-size lookup."""

    def test_cpu_only_fallback(self) -> None:
        """No VRAM reading (CPU-only probe) -> default batch size."""
        assert _recommend_batch_size(vram_gb=None) == FINE_TUNE_DEFAULT_BATCH_SIZE

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
        vram_gb: int,
        expected: int,
    ) -> None:
        assert _recommend_batch_size(vram_gb=float(vram_gb)) == expected

    def test_vram_table_is_descending(self) -> None:
        """Invariant: VRAM thresholds must be in strictly descending order."""
        thresholds = [gb for gb, _batch in _BATCH_SIZE_BY_VRAM_GB]
        assert thresholds == sorted(thresholds, reverse=True)
        assert len(thresholds) == len(set(thresholds))

    def test_custom_vram_table_is_honoured(self) -> None:
        """An operator-tuned VRAM table (from the memory bridge) wins.

        ``run_preflight`` passes
        ``app_state.bridge_config.memory.fine_tune_vram_batch_table``;
        this proves the bridge value reaches the lookup and overrides
        the module-constant tiers.
        """
        # Default tiers would map 20 GB -> 64; an operator table with a
        # 20 GB row mapping to 256 must win.
        custom = ((20.0, 256), (8.0, 32))
        assert _recommend_batch_size(vram_gb=20.0, vram_table=custom) == 256


@pytest.mark.unit
class TestProbeDrivenChecks:
    """Dependency/GPU checks derive from the effective ProbeResult."""

    def test_dependencies_pass_containerised(self) -> None:
        from synthorg.api.controllers.memory._preflight import _check_dependencies
        from synthorg.memory.embedding.fine_tune_probe_result import ProbeResult

        check = _check_dependencies(
            ProbeResult(ok=True, detail="PROBE_OK gpu=none vram_gb=0"),
            containerised=True,
        )
        assert check.status == "pass"
        assert "ephemeral fine-tune probe" in check.message

    def test_dependencies_fail_carries_probe_detail(self) -> None:
        from synthorg.api.controllers.memory._preflight import _check_dependencies
        from synthorg.memory.embedding.fine_tune_probe_result import ProbeResult

        check = _check_dependencies(
            ProbeResult(ok=False, detail="torch import failed"),
            containerised=True,
        )
        assert check.status == "fail"
        assert check.detail == "torch import failed"

    def test_dependencies_in_process_wording(self) -> None:
        from synthorg.api.controllers.memory._preflight import _check_dependencies
        from synthorg.memory.embedding.fine_tune_probe_result import ProbeResult

        check = _check_dependencies(
            ProbeResult(ok=True, detail="ML dependencies installed"),
            containerised=False,
        )
        assert check.status == "pass"
        assert check.message == "ML dependencies installed"

    def test_gpu_pass_with_vram_detail(self) -> None:
        from synthorg.api.controllers.memory._preflight import _check_gpu
        from synthorg.memory.embedding.fine_tune_probe_result import ProbeResult

        check = _check_gpu(
            ProbeResult(ok=True, gpu="Example GPU 90", vram_gb=24.0, detail="ok")
        )
        assert check.status == "pass"
        assert "Example GPU 90" in check.message
        assert check.detail == "VRAM: 24.0 GB"

    def test_gpu_warn_when_cpu_only(self) -> None:
        from synthorg.api.controllers.memory._preflight import _check_gpu
        from synthorg.memory.embedding.fine_tune_probe_result import ProbeResult

        check = _check_gpu(ProbeResult(ok=True, detail="ok"))
        assert check.status == "warn"
        assert "No GPU detected" in check.message

    def test_gpu_warn_when_probe_failed(self) -> None:
        from synthorg.api.controllers.memory._preflight import _check_gpu
        from synthorg.memory.embedding.fine_tune_probe_result import ProbeResult

        check = _check_gpu(ProbeResult(ok=False, detail="no image"))
        assert check.status == "warn"
        assert "Cannot detect GPU" in check.message

    def test_local_probe_reports_missing_deps(self) -> None:
        """Without the torch extras the local probe is honestly not ok.

        The guard is patched rather than left to the ambient environment: on a
        machine that HAS the fine-tune extra installed the probe legitimately
        reports ok, so an unpatched assertion turns on what is on the box.
        """
        from synthorg.api.controllers.memory._preflight_probe import local_probe

        with patch(
            "synthorg.memory.embedding.fine_tune._import_torch",
            side_effect=FineTuneDependencyError("torch is not installed"),
        ):
            probe = local_probe()
        assert probe.ok is False
        # The detail is what the dashboard renders, so it has to name what is
        # missing rather than merely report that something is.
        assert "torch" in probe.detail

    def test_local_probe_reports_a_package_only_install_as_not_ok(self) -> None:
        """Importing sentence-transformers is not the same as being able to train.

        ``datasets`` and ``accelerate`` live in the ``train`` extra, so a bare
        pin imports cleanly and still cannot reach stage 3. Preflight has to
        say so before the run spends stages 1 and 2.
        """
        from synthorg.api.controllers.memory._preflight_probe import local_probe

        with (
            patch(
                "synthorg.memory.embedding.fine_tune._import_torch",
                return_value=torch_double(cuda=None),
            ),
            patch(
                "synthorg.memory.embedding.fine_tune._import_sentence_transformers",
                return_value=module_double("sentence_transformers"),
            ),
            patch(
                "synthorg.memory.embedding.fine_tune.import_trainer_api",
                side_effect=FineTuneDependencyError("datasets is not installed"),
            ),
        ):
            probe = local_probe()
        assert probe.ok is False
        assert "datasets" in probe.detail


@pytest.mark.unit
class TestProbeCache:
    """``probe_fine_tune_image`` caches per (image, gpu) with a TTL."""

    async def test_second_call_within_ttl_hits_cache(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from synthorg.api.controllers.memory import _preflight_probe
        from synthorg.memory.embedding.fine_tune_docker_runner import (
            FineTuneContainerRunner,
        )
        from synthorg.memory.embedding.fine_tune_probe_result import ProbeResult
        from tests._shared.fake_clock import FakeClock

        monkeypatch.setattr(_preflight_probe, "_probe_cache", {})
        calls: list[str] = []

        async def _fake_probe(
            self: FineTuneContainerRunner, *, image: str, gpu_enabled: bool
        ) -> ProbeResult:
            calls.append(image)
            return ProbeResult(ok=True, detail="ok")

        monkeypatch.setattr(FineTuneContainerRunner, "probe", _fake_probe)
        clock = FakeClock()

        first = await _preflight_probe.probe_fine_tune_image(
            image="example.test/fine-tune:1", gpu_enabled=False, clock=clock
        )
        clock.advance(10.0)
        second = await _preflight_probe.probe_fine_tune_image(
            image="example.test/fine-tune:1", gpu_enabled=False, clock=clock
        )
        assert first == second
        assert calls == ["example.test/fine-tune:1"]

    async def test_expired_ttl_reprobes(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from synthorg.api.controllers.memory import _preflight_probe
        from synthorg.memory.embedding.fine_tune_docker_runner import (
            FineTuneContainerRunner,
        )
        from synthorg.memory.embedding.fine_tune_probe_result import ProbeResult
        from tests._shared.fake_clock import FakeClock

        monkeypatch.setattr(_preflight_probe, "_probe_cache", {})
        calls: list[str] = []

        async def _fake_probe(
            self: FineTuneContainerRunner, *, image: str, gpu_enabled: bool
        ) -> ProbeResult:
            calls.append(image)
            return ProbeResult(ok=True, detail="ok")

        monkeypatch.setattr(FineTuneContainerRunner, "probe", _fake_probe)
        clock = FakeClock()

        await _preflight_probe.probe_fine_tune_image(
            image="example.test/fine-tune:1", gpu_enabled=False, clock=clock
        )
        clock.advance(120.0)
        await _preflight_probe.probe_fine_tune_image(
            image="example.test/fine-tune:1", gpu_enabled=False, clock=clock
        )
        assert len(calls) == 2

    async def test_gpu_flag_is_part_of_the_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from synthorg.api.controllers.memory import _preflight_probe
        from synthorg.memory.embedding.fine_tune_docker_runner import (
            FineTuneContainerRunner,
        )
        from synthorg.memory.embedding.fine_tune_probe_result import ProbeResult
        from tests._shared.fake_clock import FakeClock

        monkeypatch.setattr(_preflight_probe, "_probe_cache", {})
        calls: list[bool] = []

        async def _fake_probe(
            self: FineTuneContainerRunner, *, image: str, gpu_enabled: bool
        ) -> ProbeResult:
            calls.append(gpu_enabled)
            return ProbeResult(ok=True, detail="ok")

        monkeypatch.setattr(FineTuneContainerRunner, "probe", _fake_probe)
        clock = FakeClock()

        await _preflight_probe.probe_fine_tune_image(
            image="example.test/fine-tune:1", gpu_enabled=False, clock=clock
        )
        await _preflight_probe.probe_fine_tune_image(
            image="example.test/fine-tune:1", gpu_enabled=True, clock=clock
        )
        assert calls == [False, True]


@pytest.mark.unit
class TestDeleteMemoryEntryEndpoint:
    """Direct-method coverage for ``MemoryAdminController.delete_memory_entry``."""

    async def test_returns_ok_when_backend_deletes_entry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from litestar.datastructures import State

        from synthorg.api.controllers.memory import _shared as memory_module

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
        monkeypatch.setattr(memory_module, "build_memory_service", _fake_build)
        response = await controller.delete_memory_entry.fn(
            controller,
            state=State({"app_state": make_app_state()}),
            agent_id="agent-1",
            memory_id="mem-1",
        )

        assert response.data is None
        fake_service.delete_memory_entry.assert_awaited_once_with(
            "agent-1",
            "mem-1",
        )

    async def test_raises_404_when_backend_returns_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from litestar.datastructures import State

        from synthorg.api.controllers.memory import _shared as memory_module
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
        monkeypatch.setattr(memory_module, "build_memory_service", _fake_build)
        with pytest.raises(NotFoundError):
            await controller.delete_memory_entry.fn(
                controller,
                state=State({"app_state": make_app_state()}),
                agent_id="agent-1",
                memory_id="missing",
            )
        fake_service.delete_memory_entry.assert_awaited_once_with(
            "agent-1",
            "missing",
        )

    async def test_raises_501_when_backend_unsupported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from litestar.datastructures import State

        from synthorg.api.controllers.memory import _shared as memory_module
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
        monkeypatch.setattr(memory_module, "build_memory_service", _fake_build)
        with pytest.raises(FeatureNotImplementedError) as exc_info:
            await controller.delete_memory_entry.fn(
                controller,
                state=State({"app_state": make_app_state()}),
                agent_id="agent-1",
                memory_id="mem-1",
            )
        fake_service.delete_memory_entry.assert_awaited_once_with(
            "agent-1",
            "mem-1",
        )
        assert exc_info.value.status_code == 501


@pytest.mark.unit
class TestResolveFineTuneThresholds:
    """Verify the runtime resolver consults SettingsService overrides."""

    async def test_falls_back_to_imported_defaults_when_service_missing(
        self,
    ) -> None:
        """A ``None`` service path returns the imported defaults verbatim."""
        from synthorg.api.controllers.memory._preflight import (
            _resolve_fine_tune_thresholds,
        )
        from synthorg.settings.definitions.memory_fine_tune import (
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

        from synthorg.api.controllers.memory._preflight import (
            _resolve_fine_tune_thresholds,
        )
        from synthorg.core.types import NotBlankStr
        from synthorg.settings.enums import SettingNamespace, SettingSource
        from synthorg.settings.models import SettingValue
        from synthorg.settings.service import SettingsService

        async def _fake_get(_namespace: str, key: str) -> SettingValue:
            override_value = {
                "fine_tune_default_batch_size": "256",
                "fine_tune_min_docs_required": "25",
                "fine_tune_min_docs_recommended": "75",
                "fine_tune_preflight_max_depth": "12",
                "fine_tune_preflight_walk_timeout_s": "2.5",
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
        assert thresholds.preflight_max_depth == 12
        assert thresholds.preflight_walk_timeout_s == 2.5

    async def test_unparseable_value_falls_back_to_default(self) -> None:
        """A non-integer setting value drops to the imported fallback."""
        from unittest.mock import AsyncMock

        from synthorg.api.controllers.memory._preflight import (
            _resolve_fine_tune_thresholds,
        )
        from synthorg.core.types import NotBlankStr
        from synthorg.settings.definitions.memory_fine_tune import (
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

        from synthorg.api.controllers.memory._preflight import (
            _resolve_fine_tune_thresholds,
        )
        from synthorg.settings.definitions.memory_fine_tune import (
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

        from synthorg.api.controllers.memory._preflight import _check_documents

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

        from synthorg.api.controllers.memory._preflight import _check_documents

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

        from synthorg.api.controllers.memory._preflight import _check_documents

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

        from synthorg.api.controllers.memory._preflight import _check_documents

        src = Path(str(tmp_path))
        for i in range(10):
            (src / f"doc-{i:02d}.md").write_text("x")
        check = _check_documents(
            str(src),
            min_required=10,
            min_recommended=50,
        )
        assert check.status == "warn"

    def test_depth_cap_truncates_to_warn_not_false_fail(
        self,
        tmp_path: object,
    ) -> None:
        """A tree deeper than ``max_depth`` returns a truncation warn.

        Without the cap the scan would recurse unbounded; with it the
        endpoint must surface ``warn`` (scan truncated) rather than a
        false ``fail`` from an undercount or an unbounded traversal.
        """
        from pathlib import Path

        from synthorg.api.controllers.memory._preflight import _check_documents

        root = Path(str(tmp_path))
        deep = root
        for level in range(6):
            deep = deep / f"level-{level}"
            deep.mkdir()
            (deep / f"doc-{level}.md").write_text("x")
        check = _check_documents(
            str(root),
            min_required=1,
            min_recommended=2,
            max_depth=2,
            walk_timeout_s=30.0,
        )
        assert check.status == "warn"
        assert "truncated" in check.message.lower()

    def test_deadline_truncates_to_warn(
        self,
        tmp_path: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A scan that exceeds the wall-clock deadline warns, not hangs.

        ``time.monotonic`` is advanced past the deadline on the first
        in-loop check so the bound is exercised deterministically
        without depending on real wall-clock timing.
        """
        import time as _time_mod
        from pathlib import Path

        from synthorg.api.controllers.memory._preflight import _check_documents

        src = Path(str(tmp_path))
        for i in range(30):
            (src / f"doc-{i:02d}.md").write_text("x")

        ticks = iter([0.0, 1.0, 100.0, 200.0, 300.0])

        def _fake_monotonic() -> float:
            try:
                return next(ticks)
            except StopIteration:
                return 999.0

        # ``_check_documents`` imports ``time`` locally, so patching
        # the stdlib module's ``monotonic`` is what the deadline check
        # resolves at call time.
        monkeypatch.setattr(_time_mod, "monotonic", _fake_monotonic)

        check = _check_documents(
            str(src),
            min_required=1,
            min_recommended=2,
            max_depth=64,
            walk_timeout_s=0.001,
        )
        assert check.status == "warn"
        assert "truncated" in check.message.lower()


@pytest.mark.unit
class TestListCheckpointsEndpoint:
    """Direct-method coverage for ``MemoryAdminController.list_checkpoints``."""

    async def test_no_cursor_starts_at_offset_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from litestar.datastructures import State

        from synthorg.api.controllers.memory import _shared as memory_module
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
        monkeypatch.setattr(memory_module, "build_memory_service", _fake_build)
        app_state = make_app_state(cursor_secret=CursorSecret.ephemeral())
        response = await controller.list_checkpoints.fn(
            controller,
            state=State({"app_state": app_state}),
            cursor=None,
            limit=50,
        )

        assert response.data == ()
        list_mock.assert_awaited_once_with(limit=50, offset=0)

    async def test_with_cursor_decodes_to_offset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from litestar.datastructures import State

        from synthorg.api.controllers.memory import _shared as memory_module
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
        monkeypatch.setattr(memory_module, "build_memory_service", _fake_build)
        await controller.list_checkpoints.fn(
            controller,
            state=State({"app_state": make_app_state(cursor_secret=secret)}),
            cursor=cursor,
            limit=50,
        )

        list_mock.assert_awaited_once_with(limit=50, offset=10)

    async def test_tampered_cursor_raises(self) -> None:
        from litestar.datastructures import State

        from synthorg.api.cursor import CursorSecret, InvalidCursorError

        controller = MemoryAdminController(owner=None)  # type: ignore[arg-type]
        app_state = make_app_state(cursor_secret=CursorSecret.ephemeral())
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

    async def test_no_cursor_starts_at_offset_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from litestar.datastructures import State

        from synthorg.api.controllers.memory import _shared as memory_module
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
        monkeypatch.setattr(memory_module, "build_memory_service", _fake_build)
        app_state = make_app_state(cursor_secret=CursorSecret.ephemeral())
        response = await controller.list_runs.fn(
            controller,
            state=State({"app_state": app_state}),
            cursor=None,
            limit=50,
        )

        assert response.data == ()
        list_mock.assert_awaited_once_with(limit=50, offset=0)

    async def test_with_cursor_decodes_to_offset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from litestar.datastructures import State

        from synthorg.api.controllers.memory import _shared as memory_module
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
        monkeypatch.setattr(memory_module, "build_memory_service", _fake_build)
        await controller.list_runs.fn(
            controller,
            state=State({"app_state": make_app_state(cursor_secret=secret)}),
            cursor=cursor,
            limit=50,
        )

        list_mock.assert_awaited_once_with(limit=50, offset=15)

    async def test_tampered_cursor_raises(self) -> None:
        from litestar.datastructures import State

        from synthorg.api.cursor import CursorSecret, InvalidCursorError

        controller = MemoryAdminController(owner=None)  # type: ignore[arg-type]
        app_state = make_app_state(cursor_secret=CursorSecret.ephemeral())
        with pytest.raises(InvalidCursorError):
            await controller.list_runs.fn(
                controller,
                state=State({"app_state": app_state}),
                cursor="not-a-real-cursor",
                limit=50,
            )


@pytest.mark.unit
class TestPathParamTyping:
    """The 5 admin path-param handlers carry the ``PathId`` domain type.

    Each handler annotates its identifier path params with the
    framework-level ``PathId`` constraint so a blank / over-length
    segment is rejected by Litestar before the handler body runs.
    """

    @pytest.mark.parametrize(
        ("handler_name", "param_names"),
        [
            ("resume_fine_tune", ("run_id",)),
            ("deploy_checkpoint", ("checkpoint_id",)),
            ("rollback_checkpoint", ("checkpoint_id",)),
            ("delete_checkpoint", ("checkpoint_id",)),
            ("delete_memory_entry", ("agent_id", "memory_id")),
        ],
    )
    def test_handler_path_params_use_pathid(
        self,
        handler_name: str,
        param_names: tuple[str, ...],
    ) -> None:
        import typing

        from synthorg.api.path_params import PathId

        fn = getattr(MemoryAdminController, handler_name).fn
        hints = typing.get_type_hints(fn, include_extras=True)
        for param in param_names:
            assert hints[param] == PathId, (
                f"{handler_name}.{param} must be annotated PathId, "
                f"got {hints.get(param)!r}"
            )
