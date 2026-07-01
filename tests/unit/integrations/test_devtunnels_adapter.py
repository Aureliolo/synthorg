"""Tests for the GitHub Dev Tunnels adapter."""

import asyncio
import shutil
from asyncio.subprocess import Process
from typing import override

import pytest

from synthorg.integrations.errors import TunnelError
from synthorg.integrations.tunnel.devtunnels_adapter import DevTunnelsAdapter
from synthorg.integrations.tunnel.protocol import TunnelCredentialKind

pytestmark = pytest.mark.unit


class _FakeProcess(Process):
    """``Process`` stand-in for CLI-output scraping.

    Subclasses the real ``Process`` (without calling its ``__init__``,
    which needs a live transport) so typeguard's isinstance check at
    the adapter's subprocess boundaries passes.
    """

    def __init__(
        self,
        stdout_lines: list[str],
        *,
        returncode: int | None = None,
        keep_open: bool = False,
    ) -> None:
        self.stdout = asyncio.StreamReader()
        for line in stdout_lines:
            self.stdout.feed_data(line.encode("utf-8"))
        if not keep_open:
            self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_eof()
        self._rc = returncode
        self.terminated = False

    @property
    @override
    def returncode(self) -> int | None:
        return self._rc

    @override
    def terminate(self) -> None:
        self.terminated = True
        self._rc = 0
        if self.stdout is not None:
            self.stdout.feed_eof()

    @override
    def kill(self) -> None:
        self._rc = -9

    @override
    async def wait(self) -> int:
        self._rc = self._rc if self._rc is not None else 0
        return self._rc

    @override
    async def communicate(
        self,
        input: bytes | bytearray | memoryview[int] | None = None,
    ) -> tuple[bytes, bytes]:
        data = await self.stdout.read() if self.stdout is not None else b""
        return data, b""


def _patch_binary(monkeypatch: pytest.MonkeyPatch, *, present: bool) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "devtunnel" if present else None)


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
        fake = _FakeProcess(["Not logged in.\n"], returncode=0)

        async def _spawn(*_args: object, **_kwargs: object) -> _FakeProcess:
            return fake

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)
        adapter = DevTunnelsAdapter(port=3001)
        assert await adapter.credential_configured() is False

    async def test_logged_in_output_reports_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_binary(monkeypatch, present=True)
        fake = _FakeProcess(["Logged in as octocat using GitHub.\n"], returncode=0)

        async def _spawn(*_args: object, **_kwargs: object) -> _FakeProcess:
            return fake

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)
        adapter = DevTunnelsAdapter(port=3001)
        assert await adapter.credential_configured() is True


class TestDeviceLogin:
    async def test_scrapes_verification_url_and_code(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_binary(monkeypatch, present=True)
        fake = _FakeProcess(
            [
                "To sign in, use a web browser to open the page"
                " https://github.com/login/device and enter the code"
                " ABCD-1234 to authenticate.\n",
            ],
            keep_open=True,
        )

        async def _spawn(*_args: object, **_kwargs: object) -> _FakeProcess:
            return fake

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)
        adapter = DevTunnelsAdapter(port=3001)
        prompt = await adapter.begin_login()
        assert prompt.verification_uri == "https://github.com/login/device"
        assert prompt.user_code == "ABCD-1234"
        assert prompt.already_logged_in is False
        # Complete the background drain so no task leaks.
        fake.terminate()
        login_task = adapter._login_task
        assert login_task is not None
        await login_task

    async def test_clean_exit_without_prompt_means_already_logged_in(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_binary(monkeypatch, present=True)
        fake = _FakeProcess(["Logged in as octocat.\n"], returncode=0)

        async def _spawn(*_args: object, **_kwargs: object) -> _FakeProcess:
            return fake

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)
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
        fake = _FakeProcess(["Not logged in.\n"], returncode=0)

        async def _spawn(*_args: object, **_kwargs: object) -> _FakeProcess:
            return fake

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)
        adapter = DevTunnelsAdapter(port=3001)
        with pytest.raises(TunnelError, match="GitHub login"):
            await adapter.start()

    async def test_start_scrapes_tunnel_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_binary(monkeypatch, present=True)
        status = _FakeProcess(["Logged in as octocat.\n"], returncode=0)
        host = _FakeProcess(
            [
                "Hosting port: 3001\n",
                "Connect via browser: https://abc123-3001.euw.devtunnels.ms\n",
            ],
            keep_open=True,
        )
        spawned: list[_FakeProcess] = [status, host]

        async def _spawn(*_args: object, **_kwargs: object) -> _FakeProcess:
            return spawned.pop(0)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)
        adapter = DevTunnelsAdapter(port=3001)
        url = await adapter.start()
        assert url == "https://abc123-3001.euw.devtunnels.ms"
        await adapter.stop()
        assert host.terminated is True
