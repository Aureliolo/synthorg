"""Tests for the GitHub Dev Tunnels adapter."""

import os
import platform
import shutil
import stat
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from synthorg.integrations.errors import TunnelError
from synthorg.integrations.tunnel import devtunnels_adapter
from synthorg.integrations.tunnel.devtunnels_adapter import DevTunnelsAdapter
from synthorg.integrations.tunnel.protocol import TunnelCredentialKind
from tests.unit.integrations.tunnel_process_fakes import FakePopen

pytestmark = pytest.mark.unit

type _EnvLog = list[Mapping[str, str] | None]


def _patch_binary(monkeypatch: pytest.MonkeyPatch, *, present: bool) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "devtunnel" if present else None)


def _patch_user_show(
    monkeypatch: pytest.MonkeyPatch, *, returncode: int, text: str
) -> _EnvLog:
    envs: _EnvLog = []

    async def _run(
        _args: list[str],
        *,
        timeout_seconds: float,
        env: Mapping[str, str] | None = None,
    ) -> tuple[int, str] | None:
        envs.append(env)
        return returncode, text

    monkeypatch.setattr(devtunnels_adapter, "run_cli", _run)
    return envs


def _patch_spawn(monkeypatch: pytest.MonkeyPatch, fake: FakePopen) -> _EnvLog:
    envs: _EnvLog = []

    def _spawn(
        _args: list[str], *, env: Mapping[str, str] | None = None
    ) -> subprocess.Popen[bytes]:
        envs.append(env)
        return fake

    monkeypatch.setattr(devtunnels_adapter, "spawn_cli", _spawn)
    return envs


def _adapter(tmp_path: Path, **overrides: object) -> DevTunnelsAdapter:
    kwargs: dict[str, object] = {
        "port": 3001,
        "binary_dir": tmp_path / "bin",
        "home_dir": tmp_path / "home",
    }
    kwargs.update(overrides)
    return DevTunnelsAdapter(**kwargs)  # type: ignore[arg-type]  # **dict unpacking of the typed kwargs


def _expected_env(tmp_path: Path) -> Mapping[str, str] | None:
    """The confined env the adapter passes on this host platform."""
    if sys.platform != "win32":
        return {"HOME": str(tmp_path / "home")}
    return None


class TestIdentity:
    def test_descriptor(self, tmp_path: Path) -> None:
        adapter = _adapter(tmp_path)
        assert adapter.provider_id == "devtunnels"
        assert adapter.credential_kind is TunnelCredentialKind.DEVICE_LOGIN


class TestAvailability:
    async def test_available_with_binary_on_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _patch_binary(monkeypatch, present=True)
        adapter = _adapter(tmp_path)
        available, detail = await adapter.availability()
        assert available is True
        assert detail is None

    async def test_available_when_downloadable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _patch_binary(monkeypatch, present=False)
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        monkeypatch.setattr(platform, "machine", lambda: "x86_64")
        adapter = _adapter(tmp_path)
        available, detail = await adapter.availability()
        assert available is True
        assert detail is not None
        assert "downloaded on first start" in detail

    async def test_unavailable_when_download_disabled_and_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _patch_binary(monkeypatch, present=False)
        adapter = _adapter(tmp_path, download_enabled=False)
        available, detail = await adapter.availability()
        assert available is False
        assert detail is not None
        assert "download is disabled" in detail
        assert "aka.ms/TunnelsCliDownload" in detail

    async def test_unavailable_on_unsupported_platform(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _patch_binary(monkeypatch, present=False)
        monkeypatch.setattr(platform, "machine", lambda: "i686")
        adapter = _adapter(tmp_path)
        available, detail = await adapter.availability()
        assert available is False
        assert detail is not None
        assert "No official devtunnel build" in detail

    async def test_credential_not_configured_without_binary(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _patch_binary(monkeypatch, present=False)
        adapter = _adapter(tmp_path)
        assert await adapter.credential_configured() is False


class TestCredentialCheck:
    async def test_not_logged_in_output_reports_unconfigured(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _patch_binary(monkeypatch, present=True)
        _patch_user_show(monkeypatch, returncode=0, text="Not logged in.\n")
        adapter = _adapter(tmp_path)
        assert await adapter.credential_configured() is False

    async def test_logged_in_output_reports_configured(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _patch_binary(monkeypatch, present=True)
        envs = _patch_user_show(
            monkeypatch, returncode=0, text="Logged in as octocat using GitHub.\n"
        )
        adapter = _adapter(tmp_path)
        assert await adapter.credential_configured() is True
        # The login cache lives in the confined HOME, so the status
        # check must consult the same one.
        assert envs == [_expected_env(tmp_path)]


class TestDeviceLogin:
    async def test_scrapes_verification_url_and_code(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _patch_binary(monkeypatch, present=True)
        fake = FakePopen(
            stdout_lines=[
                "To sign in, use a web browser to open the page"
                " https://github.com/login/device and enter the code"
                " ABCD-1234 to authenticate.\n",
            ],
        )
        envs = _patch_spawn(monkeypatch, fake)
        adapter = _adapter(tmp_path)
        prompt = await adapter.begin_login()
        assert prompt.verification_uri == "https://github.com/login/device"
        assert prompt.user_code == "ABCD-1234"
        assert prompt.already_logged_in is False
        assert envs == [_expected_env(tmp_path)]

    async def test_clean_exit_without_prompt_means_already_logged_in(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _patch_binary(monkeypatch, present=True)
        fake = FakePopen(stdout_lines=["Logged in as octocat.\n"], returncode=0)
        _patch_spawn(monkeypatch, fake)
        adapter = _adapter(tmp_path)
        prompt = await adapter.begin_login()
        assert prompt.already_logged_in is True

    async def test_missing_binary_with_download_disabled_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _patch_binary(monkeypatch, present=False)
        adapter = _adapter(tmp_path, download_enabled=False)
        with pytest.raises(TunnelError, match="download is disabled"):
            await adapter.begin_login()


class TestStart:
    async def test_start_requires_login(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _patch_binary(monkeypatch, present=True)
        _patch_user_show(monkeypatch, returncode=0, text="Not logged in.\n")
        adapter = _adapter(tmp_path)
        with pytest.raises(TunnelError, match="GitHub login"):
            await adapter.start()

    async def test_start_scrapes_tunnel_url(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _patch_binary(monkeypatch, present=True)
        _patch_user_show(monkeypatch, returncode=0, text="Logged in as octocat.\n")
        host = FakePopen(
            stdout_lines=[
                "Hosting port: 3001\n",
                "Connect via browser: https://abc123-3001.euw.devtunnels.ms\n",
            ],
        )
        envs = _patch_spawn(monkeypatch, host)
        adapter = _adapter(tmp_path)
        url = await adapter.start()
        assert url == "https://abc123-3001.euw.devtunnels.ms"
        assert envs == [_expected_env(tmp_path)]
        await adapter.stop()
        assert host.terminated is True


class TestConfinedEnv:
    def test_posix_confines_home_to_private_dir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        home = tmp_path / "home"
        adapter = _adapter(tmp_path)
        env = adapter._confined_env()
        assert env == {"HOME": str(home)}
        assert home.is_dir()
        if os.name == "posix":
            assert stat.S_IMODE(home.stat().st_mode) == stat.S_IRWXU

    def test_windows_returns_none(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        adapter = _adapter(tmp_path)
        assert adapter._confined_env() is None
        assert not (tmp_path / "home").exists()


class TestDownload:
    @pytest.mark.parametrize(
        ("system", "machine", "expected"),
        [
            ("Windows", "AMD64", ("win-x64", "binary")),
            ("Windows", "ARM64", ("win-arm64", "binary")),
            ("Linux", "x86_64", ("linux-x64", "binary")),
            ("Linux", "aarch64", ("linux-arm64", "binary")),
            ("Darwin", "x86_64", ("osx-x64-zip", "zip")),
            ("Darwin", "arm64", ("osx-arm64-zip", "zip")),
        ],
    )
    def test_asset_segment_table(
        self,
        monkeypatch: pytest.MonkeyPatch,
        system: str,
        machine: str,
        expected: tuple[str, str],
    ) -> None:
        monkeypatch.setattr(platform, "system", lambda: system)
        monkeypatch.setattr(platform, "machine", lambda: machine)
        assert devtunnels_adapter._asset_segment() == expected

    def test_unsupported_arch_yields_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        monkeypatch.setattr(platform, "machine", lambda: "i686")
        assert devtunnels_adapter._asset_segment() is None

    async def test_ensure_binary_downloads_bare_binary(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _patch_binary(monkeypatch, present=False)
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        monkeypatch.setattr(platform, "machine", lambda: "x86_64")
        calls: list[dict[str, object]] = []

        def _fake_download(**kwargs: object) -> Path:
            calls.append(kwargs)
            return tmp_path / "bin" / "devtunnel"

        monkeypatch.setattr(devtunnels_adapter, "download_binary", _fake_download)
        adapter = _adapter(tmp_path)
        binary = await adapter._ensure_binary()
        assert binary == tmp_path / "bin" / "devtunnel"
        assert len(calls) == 1
        assert calls[0]["url"] == "https://aka.ms/TunnelsCliDownload/linux-x64"
        assert calls[0]["extract"] is None

    async def test_ensure_binary_extracts_macos_zip(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _patch_binary(monkeypatch, present=False)
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        monkeypatch.setattr(platform, "machine", lambda: "arm64")
        calls: list[dict[str, object]] = []

        def _fake_download(**kwargs: object) -> Path:
            calls.append(kwargs)
            return tmp_path / "bin" / "devtunnel"

        monkeypatch.setattr(devtunnels_adapter, "download_binary", _fake_download)
        adapter = _adapter(tmp_path)
        await adapter._ensure_binary()
        assert calls[0]["url"] == "https://aka.ms/TunnelsCliDownload/osx-arm64-zip"
        assert calls[0]["extract"] is not None

    async def test_ensure_binary_prefers_previous_download(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _patch_binary(monkeypatch, present=False)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        name = "devtunnel.exe" if sys.platform == "win32" else "devtunnel"
        existing = bin_dir / name
        existing.write_bytes(b"")
        adapter = _adapter(tmp_path)
        assert await adapter._ensure_binary() == existing
