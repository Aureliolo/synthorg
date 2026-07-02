"""Tests for the Cloudflare quick-tunnel adapter."""

import shutil
import subprocess
from pathlib import Path

import pytest

from synthorg.integrations.errors import TunnelError
from synthorg.integrations.tunnel import cloudflare_adapter
from synthorg.integrations.tunnel.cloudflare_adapter import (
    CloudflareQuickTunnelAdapter,
)
from synthorg.integrations.tunnel.protocol import TunnelCredentialKind
from tests.unit.integrations.tunnel_process_fakes import FakePopen

pytestmark = pytest.mark.unit

_QUICK_URL = "https://witty-otter.trycloudflare.com"


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
        fake = FakePopen(
            stderr_lines=[
                "2026-07-02 INF Requesting new quick Tunnel on trycloudflare.com...\n",
                f"2026-07-02 INF |  {_QUICK_URL}  |\n",
            ]
        )

        def _spawn(_args: list[str]) -> subprocess.Popen[bytes]:
            return fake

        monkeypatch.setattr(cloudflare_adapter, "spawn_cli", _spawn)
        adapter = CloudflareQuickTunnelAdapter(port=3001, binary_dir=tmp_path)
        url = await adapter.start()
        assert url == _QUICK_URL
        assert await adapter.get_url() == _QUICK_URL
        await adapter.stop()
        assert fake.terminated is True
        assert await adapter.get_url() is None

    async def test_start_fails_without_url_in_output(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        binary = tmp_path / "cloudflared"
        binary.write_bytes(b"")
        monkeypatch.setattr(shutil, "which", lambda _name: str(binary))
        fake = FakePopen(stderr_lines=["no url in this output\n"])

        def _spawn(_args: list[str]) -> subprocess.Popen[bytes]:
            return fake

        monkeypatch.setattr(cloudflare_adapter, "spawn_cli", _spawn)
        adapter = CloudflareQuickTunnelAdapter(port=3001, binary_dir=tmp_path)
        with pytest.raises(TunnelError, match="no quick-tunnel URL"):
            await adapter.start()
        assert fake.terminated is True
