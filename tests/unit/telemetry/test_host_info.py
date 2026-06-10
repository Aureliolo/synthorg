"""Tests for the Docker daemon enrichment helper."""

from unittest.mock import AsyncMock

import pytest

from synthorg.telemetry import host_info


@pytest.mark.unit
class TestFetchDockerInfoUnavailablePaths:
    """Every no-socket / no-daemon path collapses to the marker payload."""

    async def test_socket_missing_returns_marker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "synthorg.telemetry.host_info.os.path.exists", lambda _path: False
        )
        result = await host_info.fetch_docker_info()
        assert result == {
            "docker_info_available": False,
            "docker_info_unavailable_reason": "socket_not_mounted",
        }

    async def test_aiodocker_not_installed_returns_marker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "synthorg.telemetry.host_info.os.path.exists", lambda _path: True
        )

        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "aiodocker":
                msg = "simulated missing aiodocker"
                raise ImportError(msg)
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr("builtins.__import__", fake_import)

        result = await host_info.fetch_docker_info()
        assert result["docker_info_available"] is False
        assert result["docker_info_unavailable_reason"] == "aiodocker_not_installed"

    async def test_daemon_unreachable_returns_marker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pytest.importorskip("aiodocker")
        import aiodocker

        monkeypatch.setattr(
            "synthorg.telemetry.host_info.os.path.exists", lambda _path: True
        )

        def raise_construction(*_a: object, **_k: object) -> object:
            msg = "simulated daemon unreachable"
            raise OSError(msg)

        monkeypatch.setattr(aiodocker, "Docker", raise_construction)

        result = await host_info.fetch_docker_info()
        assert result["docker_info_available"] is False
        assert result["docker_info_unavailable_reason"] == "daemon_unreachable"

    async def test_info_call_raises_returns_marker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pytest.importorskip("aiodocker")
        import aiodocker

        monkeypatch.setattr(
            "synthorg.telemetry.host_info.os.path.exists", lambda _path: True
        )

        mock_client = AsyncMock()
        mock_client.system.info = AsyncMock(side_effect=RuntimeError("boom"))
        monkeypatch.setattr(aiodocker, "Docker", lambda *_a, **_k: mock_client)

        result = await host_info.fetch_docker_info()
        assert result["docker_info_available"] is False
        assert result["docker_info_unavailable_reason"] == "daemon_unreachable"
        # ``async with`` cleanup runs via ``__aexit__``; verify it fired
        # instead of the legacy ``close()`` path removed in V3.
        mock_client.__aexit__.assert_awaited_once()

    async def test_info_non_dict_returns_marker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pytest.importorskip("aiodocker")
        import aiodocker

        monkeypatch.setattr(
            "synthorg.telemetry.host_info.os.path.exists", lambda _path: True
        )

        mock_client = AsyncMock()
        mock_client.system.info = AsyncMock(return_value="not a dict")
        monkeypatch.setattr(aiodocker, "Docker", lambda *_a, **_k: mock_client)

        result = await host_info.fetch_docker_info()
        assert result["docker_info_available"] is False
        assert result["docker_info_unavailable_reason"] == "daemon_unreachable"


@pytest.mark.unit
class TestFetchDockerInfoSuccess:
    """Happy-path extraction and the NVIDIA runtime flag."""

    @pytest.fixture
    def mock_daemon(self, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
        """Patch socket + aiodocker to return a configurable /info dict."""
        pytest.importorskip("aiodocker")
        import aiodocker

        monkeypatch.setattr(
            "synthorg.telemetry.host_info.os.path.exists", lambda _path: True
        )

        mock_client = AsyncMock()
        monkeypatch.setattr(aiodocker, "Docker", lambda *_a, **_k: mock_client)
        return mock_client

    async def test_full_info_extracts_allowlisted_fields(
        self, mock_daemon: AsyncMock
    ) -> None:
        mock_daemon.system.info = AsyncMock(
            return_value={
                "ServerVersion": "27.3.1",
                "OperatingSystem": "Docker Desktop",
                "OSType": "linux",
                "OSVersion": "",
                "Architecture": "x86_64",
                "KernelVersion": "6.10.14-linuxkit",
                "Driver": "overlay2",
                "DefaultRuntime": "runc",
                "Isolation": "",
                "NCPU": 8,
                "MemTotal": 8589934592,
                "Runtimes": {
                    "runc": {"path": "/usr/bin/runc"},
                    "nvidia": {"path": "/usr/bin/nvidia-container-runtime"},
                },
                "Name": "secret-hostname-should-not-leak",
            }
        )
        result = await host_info.fetch_docker_info()
        assert result["docker_info_available"] is True
        assert result["docker_server_version"] == "27.3.1"
        assert result["docker_operating_system"] == "Docker Desktop"
        assert result["docker_os_type"] == "linux"
        assert result["docker_architecture"] == "x86_64"
        assert result["docker_kernel_version"] == "6.10.14-linuxkit"
        assert result["docker_storage_driver"] == "overlay2"
        assert result["docker_default_runtime"] == "runc"
        assert result["docker_ncpu"] == 8
        assert result["docker_mem_total"] == 8589934592
        assert result["docker_gpu_runtime_nvidia_available"] is True

        # Empty strings from the daemon are dropped (not emitted).
        assert "docker_os_version" not in result
        assert "docker_isolation" not in result

        # Hostname never leaves the process.
        assert "Name" not in result
        assert "docker_name" not in result

    async def test_missing_nvidia_runtime_flags_false(
        self, mock_daemon: AsyncMock
    ) -> None:
        mock_daemon.system.info = AsyncMock(
            return_value={
                "ServerVersion": "27.3.1",
                "Runtimes": {"runc": {"path": "/usr/bin/runc"}},
            }
        )
        result = await host_info.fetch_docker_info()
        assert result["docker_gpu_runtime_nvidia_available"] is False

    async def test_no_runtimes_key_flags_false(self, mock_daemon: AsyncMock) -> None:
        mock_daemon.system.info = AsyncMock(
            return_value={"ServerVersion": "27.3.1"},
        )
        result = await host_info.fetch_docker_info()
        assert result["docker_gpu_runtime_nvidia_available"] is False

    async def test_non_int_cpu_or_memory_dropped(self, mock_daemon: AsyncMock) -> None:
        mock_daemon.system.info = AsyncMock(
            return_value={
                "ServerVersion": "27.3.1",
                "NCPU": "eight",  # Bad type -- drop silently.
                "MemTotal": True,  # Bool is rejected even though bool subclasses int.
            }
        )
        result = await host_info.fetch_docker_info()
        assert "docker_ncpu" not in result
        assert "docker_mem_total" not in result

    async def test_long_strings_truncated_to_cap(self, mock_daemon: AsyncMock) -> None:
        long_os = "A" * 200
        mock_daemon.system.info = AsyncMock(
            return_value={"OperatingSystem": long_os},
        )
        result = await host_info.fetch_docker_info()
        assert len(result["docker_operating_system"]) == 64

    async def test_async_with_cleanup_runs_on_success(
        self, mock_daemon: AsyncMock
    ) -> None:
        """`async with` calls ``__aexit__`` once after a successful fetch."""
        mock_daemon.system.info = AsyncMock(return_value={"ServerVersion": "27.3.1"})
        await host_info.fetch_docker_info()
        mock_daemon.__aexit__.assert_awaited_once()


@pytest.mark.unit
class TestFetchDockerInfoNeverRaises:
    """The fetch must never raise -- telemetry is best-effort."""

    async def test_exception_in_cleanup_is_swallowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failing ``__aexit__`` (async-with cleanup) does not bubble up."""
        pytest.importorskip("aiodocker")
        import aiodocker

        monkeypatch.setattr(
            "synthorg.telemetry.host_info.os.path.exists", lambda _path: True
        )
        mock_client = AsyncMock()
        mock_client.system.info = AsyncMock(return_value={"ServerVersion": "27.3.1"})
        mock_client.__aexit__ = AsyncMock(side_effect=RuntimeError("cleanup failure"))
        monkeypatch.setattr(aiodocker, "Docker", lambda *_a, **_k: mock_client)

        result = await host_info.fetch_docker_info()
        # The cleanup exception is caught by the outer try/except in
        # fetch_docker_info; the result collapses to the unavailable
        # marker rather than raising or silently returning a stale
        # success payload.
        assert result["docker_info_available"] is False
        assert result["docker_info_unavailable_reason"] == "daemon_unreachable"
