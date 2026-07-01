"""Tests for the Cloudflare quick-tunnel adapter."""

import asyncio
import shutil
from asyncio.subprocess import Process
from pathlib import Path
from typing import override

import pytest

from synthorg.integrations.errors import TunnelError
from synthorg.integrations.tunnel import cloudflare_adapter
from synthorg.integrations.tunnel.cloudflare_adapter import (
    CloudflareQuickTunnelAdapter,
)
from synthorg.integrations.tunnel.protocol import TunnelCredentialKind

pytestmark = pytest.mark.unit

_QUICK_URL = "https://witty-otter.trycloudflare.com"


class _FakeProcess(Process):
    """``Process`` stand-in for URL scraping.

    Subclasses the real ``Process`` (without calling its ``__init__``,
    which needs a live transport) so typeguard's isinstance check at
    the ``terminate_process``/``wait_for_pattern`` boundary passes.
    """

    def __init__(self, stderr_lines: list[str]) -> None:
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        for line in stderr_lines:
            self.stderr.feed_data(line.encode("utf-8"))
        self.stderr.feed_eof()
        self._rc: int | None = None
        self.terminated = False

    @property
    @override
    def returncode(self) -> int | None:
        return self._rc

    @override
    def terminate(self) -> None:
        self.terminated = True
        self._rc = 0

    @override
    def kill(self) -> None:
        self._rc = -9

    @override
    async def wait(self) -> int:
        self._rc = self._rc if self._rc is not None else 0
        return self._rc


class TestIdentity:
    def test_descriptor(self) -> None:
        adapter = CloudflareQuickTunnelAdapter(port=3001)
        assert adapter.provider_id == "cloudflare"
        assert adapter.credential_kind is TunnelCredentialKind.NONE

    async def test_credential_is_always_configured(self) -> None:
        adapter = CloudflareQuickTunnelAdapter(port=3001)
        assert await adapter.credential_configured() is True


class TestAvailability:
    async def test_available_with_binary_on_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(shutil, "which", lambda _name: str(tmp_path / "cf"))
        adapter = CloudflareQuickTunnelAdapter(port=3001, binary_dir=tmp_path)
        available, detail = await adapter.availability()
        assert available is True
        assert detail is None

    async def test_unavailable_when_download_disabled_and_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(shutil, "which", lambda _name: None)
        adapter = CloudflareQuickTunnelAdapter(
            port=3001, download_enabled=False, binary_dir=tmp_path
        )
        available, detail = await adapter.availability()
        assert available is False
        assert detail is not None
        assert "download is disabled" in detail

    async def test_start_without_binary_and_download_disabled_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(shutil, "which", lambda _name: None)
        adapter = CloudflareQuickTunnelAdapter(
            port=3001, download_enabled=False, binary_dir=tmp_path
        )
        with pytest.raises(TunnelError, match="download is disabled"):
            await adapter.start()


class TestStart:
    async def test_start_scrapes_quick_tunnel_url(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        binary = tmp_path / "cloudflared"
        binary.write_bytes(b"")
        monkeypatch.setattr(shutil, "which", lambda _name: str(binary))
        fake = _FakeProcess(
            [
                "2026-07-02 INF Requesting new quick Tunnel on trycloudflare.com...\n",
                f"2026-07-02 INF |  {_QUICK_URL}  |\n",
            ]
        )

        async def _spawn(*_args: object, **_kwargs: object) -> _FakeProcess:
            return fake

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)
        adapter = CloudflareQuickTunnelAdapter(port=3001, binary_dir=tmp_path)
        url = await adapter.start()
        assert url == _QUICK_URL
        assert await adapter.get_url() == _QUICK_URL
        await adapter.stop()
        assert fake.terminated is True
        assert await adapter.get_url() is None

    async def test_start_times_out_without_url(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        binary = tmp_path / "cloudflared"
        binary.write_bytes(b"")
        monkeypatch.setattr(shutil, "which", lambda _name: str(binary))
        monkeypatch.setattr(cloudflare_adapter, "_START_TIMEOUT_SECONDS", 0.05)
        fake = _FakeProcess(["no url in this output\n"])

        async def _spawn(*_args: object, **_kwargs: object) -> _FakeProcess:
            return fake

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)
        adapter = CloudflareQuickTunnelAdapter(port=3001, binary_dir=tmp_path)
        with pytest.raises(TunnelError, match="no quick-tunnel URL"):
            await adapter.start()
