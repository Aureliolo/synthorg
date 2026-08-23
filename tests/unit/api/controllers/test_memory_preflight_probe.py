"""Probe-target derivation + preflight handler wiring tests.

Covers the backend-aware probe derivation (``resolve_probe_target`` /
``resolve_probe_gpu_default``), the failed-probe caching contract, the
disk-space degradation fallback, and the ``run_preflight`` handler's
probe / timeout / batch-size wiring.
"""

import asyncio
import shutil
from types import SimpleNamespace

import pytest
from litestar.datastructures import State

from synthorg.api.controllers.memory import _preflight, _preflight_probe
from synthorg.api.controllers.memory import fine_tune as fine_tune_module
from synthorg.api.controllers.memory._preflight import _check_disk_space
from synthorg.api.controllers.memory._preflight_probe import (
    resolve_probe_gpu_default,
    resolve_probe_target,
)
from synthorg.api.controllers.memory.fine_tune import MemoryFineTuneController
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.memory.embedding import fine_tune as fine_tune_embedding
from synthorg.memory.embedding.fine_tune_docker_runner import (
    FineTuneContainerRunner,
)
from synthorg.memory.embedding.fine_tune_models import (
    FineTuneExecutionConfig,
    FineTuneRequest,
)
from synthorg.memory.embedding.fine_tune_probe_result import ProbeResult
from synthorg.memory.errors import FineTuneDependencyError
from synthorg.settings.errors import SettingNotFoundError
from synthorg.settings.service import SettingsService
from tests._shared import make_app_state, mock_of
from tests._shared.fake_clock import FakeClock

pytestmark = pytest.mark.unit


def _settings_service(value: str) -> SettingsService:
    async def _get(_namespace: str, _key: str) -> SimpleNamespace:
        return SimpleNamespace(value=value)

    return mock_of[SettingsService](get=_get)  # type: ignore[no-any-return]


def _missing_settings_service() -> SettingsService:
    async def _get(_namespace: str, key: str) -> SimpleNamespace:
        raise SettingNotFoundError(key)

    return mock_of[SettingsService](get=_get)  # type: ignore[no-any-return]


class TestResolveProbeGpuDefault:
    async def test_no_service_defaults_false(self) -> None:
        assert await resolve_probe_gpu_default(None) is False

    async def test_missing_setting_defaults_false(self) -> None:
        service = _missing_settings_service()
        assert await resolve_probe_gpu_default(service) is False

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("true", True), (" True ", True), ("false", False), ("1", False)],
    )
    async def test_parses_setting_value(self, raw: str, expected: bool) -> None:
        service = _settings_service(raw)
        assert await resolve_probe_gpu_default(service) is expected


class TestResolveProbeTarget:
    async def test_explicit_docker_wins(self) -> None:
        request = FineTuneRequest(
            source_dir="docs",
            execution=FineTuneExecutionConfig(
                backend="docker",
                image="example.test/fine-tune:1",
                gpu_enabled=True,
            ),
        )
        image, gpu = await resolve_probe_target(request, None)
        assert image == "example.test/fine-tune:1"
        assert gpu is True

    async def test_unset_execution_derives_from_cache_and_setting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from synthorg.memory.embedding import fine_tune_image_resolution

        monkeypatch.setattr(
            fine_tune_image_resolution,
            "get_resolved_fine_tune_image",
            lambda: "example.test/fine-tune:cache",
        )
        request = FineTuneRequest(source_dir="docs")
        image, gpu = await resolve_probe_target(request, _settings_service("true"))
        assert image == "example.test/fine-tune:cache"
        assert gpu is True

    async def test_explicit_in_process_skips_container_probe(self) -> None:
        request = FineTuneRequest(
            source_dir="docs",
            execution=FineTuneExecutionConfig(backend="in-process"),
        )
        assert await resolve_probe_target(request, None) == ("", False)


class TestFailedProbeCaching:
    async def test_failed_probe_is_cached_for_the_ttl(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A broken daemon is probed once per TTL, not once per poll."""
        monkeypatch.setattr(_preflight_probe, "_probe_cache", {})
        calls: list[str] = []

        async def _fake_probe(
            self: FineTuneContainerRunner, *, image: str, gpu_enabled: bool
        ) -> ProbeResult:
            calls.append(image)
            return ProbeResult(ok=False, detail="daemon gone")

        monkeypatch.setattr(FineTuneContainerRunner, "probe", _fake_probe)
        clock = FakeClock()

        first = await _preflight_probe.probe_fine_tune_image(
            image="example.test/fine-tune:1", gpu_enabled=False, clock=clock
        )
        clock.advance(10.0)
        second = await _preflight_probe.probe_fine_tune_image(
            image="example.test/fine-tune:1", gpu_enabled=False, clock=clock
        )
        assert first.ok is False
        assert first == second
        assert calls == ["example.test/fine-tune:1"]


class TestLocalProbeDependencyFailures:
    """Both dependency branches report, rather than one failing silently."""

    def test_a_missing_dependency_is_reported_not_raised(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The commonest failure: the extra is simply not installed."""

        def _absent() -> object:
            msg = "sentence-transformers is not installed"
            raise FineTuneDependencyError(msg)

        monkeypatch.setattr(
            fine_tune_embedding, "verify_fine_tune_dependencies", _absent
        )

        result = _preflight_probe.local_probe()

        assert result.ok is False
        assert "sentence-transformers" in (result.detail or "")

    def test_an_unexpected_dependency_error_still_reports(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A half-installed stack raises shapes the typed branch never sees.

        Left unhandled it would surface as a 500 rather than a preflight that
        tells the operator what is wrong with their deployment.
        """

        def _explodes() -> object:
            msg = "libtorch_cpu.so has the wrong ABI"
            raise ValueError(msg)

        monkeypatch.setattr(
            fine_tune_embedding, "verify_fine_tune_dependencies", _explodes
        )

        result = _preflight_probe.local_probe()

        assert result.ok is False
        assert "dependency check failed" in (result.detail or "")

    def test_a_critical_error_is_never_swallowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``reraise_critical`` must still win over the reporting branch."""

        def _fatal() -> object:
            raise MemoryError

        monkeypatch.setattr(
            fine_tune_embedding, "verify_fine_tune_dependencies", _fatal
        )

        with pytest.raises(MemoryError):
            _preflight_probe.local_probe()


class TestDiskSpaceDegradation:
    def test_disk_usage_failure_degrades_to_warn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(_path: object) -> object:
            msg = "stale handle"
            raise OSError(msg)

        monkeypatch.setattr(shutil, "disk_usage", _boom)
        check = _check_disk_space("does-not-matter")
        assert check.status == "warn"
        assert "Could not check disk space" in check.message


class TestRunPreflightHandler:
    """Direct-method coverage for ``MemoryFineTuneController.run_preflight``."""

    def _request(self) -> FineTuneRequest:
        return FineTuneRequest(source_dir="docs")

    async def test_docker_probe_feeds_checks_and_batch_size(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        probe = ProbeResult(ok=True, gpu="Example GPU 90", vram_gb=24.0, detail="ok")

        async def _fake_target(
            _request: FineTuneRequest, _service: object
        ) -> tuple[str, bool]:
            return "example.test/fine-tune:1", True

        async def _fake_probe(
            *, image: str, gpu_enabled: bool, clock: object
        ) -> ProbeResult:
            return probe

        monkeypatch.setattr(fine_tune_module, "resolve_probe_target", _fake_target)
        monkeypatch.setattr(fine_tune_module, "probe_fine_tune_image", _fake_probe)
        controller = MemoryFineTuneController(owner=None)  # type: ignore[arg-type]
        response = await controller.run_preflight.fn(
            controller,
            state=State({"app_state": make_app_state()}),
            data=self._request(),
        )

        checks = {c.name: c for c in response.data.checks}
        assert checks["dependencies"].status == "pass"
        assert "ephemeral fine-tune probe" in checks["dependencies"].message
        assert checks["gpu"].status == "pass"
        # 24 GB clears the 16 GB tier of the default VRAM table.
        assert response.data.recommended_batch_size == 64

    async def test_failed_docker_probe_yields_no_batch_size(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _fake_target(
            _request: FineTuneRequest, _service: object
        ) -> tuple[str, bool]:
            return "example.test/fine-tune:1", False

        async def _fake_probe(
            *, image: str, gpu_enabled: bool, clock: object
        ) -> ProbeResult:
            return ProbeResult(ok=False, detail="image missing")

        monkeypatch.setattr(fine_tune_module, "resolve_probe_target", _fake_target)
        monkeypatch.setattr(fine_tune_module, "probe_fine_tune_image", _fake_probe)
        controller = MemoryFineTuneController(owner=None)  # type: ignore[arg-type]
        response = await controller.run_preflight.fn(
            controller,
            state=State({"app_state": make_app_state()}),
            data=self._request(),
        )

        checks = {c.name: c for c in response.data.checks}
        assert checks["dependencies"].status == "fail"
        assert response.data.recommended_batch_size is None

    async def test_in_process_target_skips_container_probe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _fake_target(
            _request: FineTuneRequest, _service: object
        ) -> tuple[str, bool]:
            return "", False

        probed: list[str] = []

        async def _fake_probe(
            *, image: str, gpu_enabled: bool, clock: object
        ) -> ProbeResult:
            probed.append(image)
            return ProbeResult(ok=True, detail="ok")

        monkeypatch.setattr(fine_tune_module, "resolve_probe_target", _fake_target)
        monkeypatch.setattr(fine_tune_module, "probe_fine_tune_image", _fake_probe)
        # With no image the handler falls through to the in-process probe,
        # which really imports torch, transformers and datasets. That is
        # correct of the probe and wrong for a unit test: on a machine
        # carrying the fine-tune extra it loads the whole ML stack and blows
        # the wall-clock budget. This test is about which probe is chosen.
        monkeypatch.setattr(
            _preflight,
            "local_probe",
            lambda: ProbeResult(ok=True, detail="deps present"),
        )
        controller = MemoryFineTuneController(owner=None)  # type: ignore[arg-type]
        response = await controller.run_preflight.fn(
            controller,
            state=State({"app_state": make_app_state()}),
            data=self._request(),
        )

        assert probed == []
        assert response.data.checks

    async def test_probe_timeout_maps_to_503(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _fake_target(
            _request: FineTuneRequest, _service: object
        ) -> tuple[str, bool]:
            return "example.test/fine-tune:1", False

        async def _hanging_probe(
            *, image: str, gpu_enabled: bool, clock: object
        ) -> ProbeResult:
            await asyncio.sleep(5.0)
            return ProbeResult(ok=True, detail="ok")

        monkeypatch.setattr(fine_tune_module, "resolve_probe_target", _fake_target)
        monkeypatch.setattr(fine_tune_module, "probe_fine_tune_image", _hanging_probe)
        monkeypatch.setattr(fine_tune_module, "_PROBE_REQUEST_CEILING_S", 0.05)
        controller = MemoryFineTuneController(owner=None)  # type: ignore[arg-type]
        with pytest.raises(ServiceUnavailableError, match="probe timed out"):
            await controller.run_preflight.fn(
                controller,
                state=State({"app_state": make_app_state()}),
                data=self._request(),
            )
