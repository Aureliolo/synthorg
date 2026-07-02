"""Tests for the GitHub Dev Tunnels adapter."""

import shutil
import subprocess

import pytest

from synthorg.integrations.errors import TunnelError
from synthorg.integrations.tunnel import devtunnels_adapter
from synthorg.integrations.tunnel.devtunnels_adapter import DevTunnelsAdapter
from synthorg.integrations.tunnel.protocol import TunnelCredentialKind
from tests.unit.integrations.tunnel_process_fakes import FakePopen

pytestmark = pytest.mark.unit


def _patch_binary(monkeypatch: pytest.MonkeyPatch, *, present: bool) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "devtunnel" if present else None)


def _patch_user_show(
    monkeypatch: pytest.MonkeyPatch, *, returncode: int, text: str
) -> None:
    async def _run(
        _args: list[str], *, timeout_seconds: float
    ) -> tuple[int, str] | None:
        return returncode, text

    monkeypatch.setattr(devtunnels_adapter, "run_cli", _run)


class TestIdentity:
    def test_descriptor(self) -> None:
        adapter = DevTunnelsAdapter(port=3001)
        assert adapter.provider_id == "devtunnels"
        assert adapter.credential_kind is TunnelCredentialKind.DEVICE_LOGIN


class TestAvailability:
    async def test_unavailable_without_binary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_binary(monkeypatch, present=False)
        adapter = DevTunnelsAdapter(port=3001)
        available, detail = await adapter.availability()
        assert available is False
        assert detail is not None
        assert "aka.ms/TunnelsCliDownload" in detail

    async def test_credential_not_configured_without_binary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_binary(monkeypatch, present=False)
        adapter = DevTunnelsAdapter(port=3001)
        assert await adapter.credential_configured() is False


class TestCredentialCheck:
    async def test_not_logged_in_output_reports_unconfigured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_binary(monkeypatch, present=True)
        _patch_user_show(monkeypatch, returncode=0, text="Not logged in.\n")
        adapter = DevTunnelsAdapter(port=3001)
        assert await adapter.credential_configured() is False

    async def test_logged_in_output_reports_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_binary(monkeypatch, present=True)
        _patch_user_show(
            monkeypatch, returncode=0, text="Logged in as octocat using GitHub.\n"
        )
        adapter = DevTunnelsAdapter(port=3001)
        assert await adapter.credential_configured() is True


class TestDeviceLogin:
    async def test_scrapes_verification_url_and_code(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_binary(monkeypatch, present=True)
        fake = FakePopen(
            stdout_lines=[
                "To sign in, use a web browser to open the page"
                " https://github.com/login/device and enter the code"
                " ABCD-1234 to authenticate.\n",
            ],
        )

        def _spawn(_args: list[str]) -> subprocess.Popen[bytes]:
            return fake

        monkeypatch.setattr(devtunnels_adapter, "spawn_cli", _spawn)
        adapter = DevTunnelsAdapter(port=3001)
        prompt = await adapter.begin_login()
        assert prompt.verification_uri == "https://github.com/login/device"
        assert prompt.user_code == "ABCD-1234"
        assert prompt.already_logged_in is False

    async def test_clean_exit_without_prompt_means_already_logged_in(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_binary(monkeypatch, present=True)
        fake = FakePopen(stdout_lines=["Logged in as octocat.\n"], returncode=0)

        def _spawn(_args: list[str]) -> subprocess.Popen[bytes]:
            return fake

        monkeypatch.setattr(devtunnels_adapter, "spawn_cli", _spawn)
        adapter = DevTunnelsAdapter(port=3001)
        prompt = await adapter.begin_login()
        assert prompt.already_logged_in is True

    async def test_missing_binary_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_binary(monkeypatch, present=False)
        adapter = DevTunnelsAdapter(port=3001)
        with pytest.raises(TunnelError, match="TunnelsCliDownload"):
            await adapter.begin_login()


class TestStart:
    async def test_start_requires_login(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_binary(monkeypatch, present=True)
        _patch_user_show(monkeypatch, returncode=0, text="Not logged in.\n")
        adapter = DevTunnelsAdapter(port=3001)
        with pytest.raises(TunnelError, match="GitHub login"):
            await adapter.start()

    async def test_start_scrapes_tunnel_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_binary(monkeypatch, present=True)
        _patch_user_show(monkeypatch, returncode=0, text="Logged in as octocat.\n")
        host = FakePopen(
            stdout_lines=[
                "Hosting port: 3001\n",
                "Connect via browser: https://abc123-3001.euw.devtunnels.ms\n",
            ],
        )

        def _spawn(_args: list[str]) -> subprocess.Popen[bytes]:
            return host

        monkeypatch.setattr(devtunnels_adapter, "spawn_cli", _spawn)
        adapter = DevTunnelsAdapter(port=3001)
        url = await adapter.start()
        assert url == "https://abc123-3001.euw.devtunnels.ms"
        await adapter.stop()
        assert host.terminated is True
