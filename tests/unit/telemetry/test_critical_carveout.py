# module-kind: tests
"""Critical-error carve-out for telemetry's best-effort except sites.

Telemetry is strictly best-effort: ordinary backend failures degrade to
a ``TELEMETRY_REPORT_FAILED`` warning. ``MemoryError`` /
``RecursionError`` must escape those handlers so catastrophic
interpreter state is never absorbed as a telemetry warning.
"""

import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from synthorg.telemetry import host_info
from synthorg.telemetry.collector import TelemetryCollector, _SessionSummaryParams
from synthorg.telemetry.config import TelemetryBackend, TelemetryConfig

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def clear_synthorg_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scrub the telemetry env chain so config inputs are explicit."""
    monkeypatch.delenv("SYNTHORG_TELEMETRY_ENABLED", raising=False)
    monkeypatch.delenv("SYNTHORG_TELEMETRY_ENV", raising=False)
    monkeypatch.delenv("SYNTHORG_TELEMETRY_ENV_BAKED", raising=False)
    for marker in ("CI", "GITLAB_CI", "BUILDKITE", "JENKINS_URL"):
        monkeypatch.delenv(marker, raising=False)
    for name in list(os.environ):
        if name.startswith("RUNPOD_"):
            monkeypatch.delenv(name, raising=False)


class TestDockerProbeCarveout:
    """``fetch_docker_info`` swallows daemon failures, not MemoryError."""

    @pytest.fixture(autouse=True)
    def socket_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "synthorg.telemetry.host_info.os.path.exists", lambda _path: True
        )

    async def test_construction_memory_error_propagates(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pytest.importorskip("aiodocker")
        import aiodocker

        def _boom(*_a: object, **_k: object) -> object:
            raise MemoryError

        monkeypatch.setattr(aiodocker, "Docker", _boom)
        with pytest.raises(MemoryError):
            await host_info.fetch_docker_info()

    async def test_info_call_memory_error_propagates(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pytest.importorskip("aiodocker")
        import aiodocker

        mock_client = AsyncMock()
        mock_client.system.info = AsyncMock(side_effect=MemoryError)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr(aiodocker, "Docker", lambda *_a, **_k: mock_client)

        with pytest.raises(MemoryError):
            await host_info.fetch_docker_info()


class TestCollectorShutdownCarveout:
    """Shutdown's best-effort steps re-raise critical errors."""

    async def test_snapshot_provider_memory_error_propagates(
        self,
        tmp_path: Path,
    ) -> None:
        def _boom() -> _SessionSummaryParams:
            raise MemoryError

        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        collector = TelemetryCollector(
            config=config,
            data_dir=tmp_path,
            session_summary_snapshot_provider=_boom,
        )
        # Force the deployment-id-loaded state shutdown() gates on.
        collector._deployment_id = "dep-test"

        with pytest.raises(MemoryError):
            await collector.shutdown()
