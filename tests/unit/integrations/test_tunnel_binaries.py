"""Tests for the shared tunnel binary-download plumbing."""

import stat
import sys
import tarfile
import zipfile
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest

from synthorg.integrations.errors import TunnelError
from synthorg.integrations.tunnel import _binaries
from synthorg.integrations.tunnel._binaries import (
    default_binary_dir,
    default_devtunnels_home_dir,
    default_state_dir,
    download_binary,
    extract_tgz_member,
    extract_zip_member,
)

pytestmark = pytest.mark.unit


class _FakeResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


class _FakeClient:
    def __init__(self, payload: bytes, requested: list[str]) -> None:
        self._payload = payload
        self._requested = requested

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def get(self, url: str) -> _FakeResponse:
        self._requested.append(url)
        return _FakeResponse(self._payload)


def _install_fake_http(monkeypatch: pytest.MonkeyPatch, payload: bytes) -> list[str]:
    requested: list[str] = []
    fake = SimpleNamespace(Client=lambda **_kwargs: _FakeClient(payload, requested))
    monkeypatch.setattr(_binaries, "httpx", fake)
    return requested


def _tgz_bytes(tmp_path: Path, member_name: str, content: bytes) -> Path:
    src = tmp_path / member_name
    src.write_bytes(content)
    archive = tmp_path / "asset.tgz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(src, arcname=f"nested/{member_name}")
    src.unlink()
    return archive


class TestDirLayout:
    def test_state_dir_is_home_dot_synthorg(self) -> None:
        assert default_state_dir() == Path.home() / ".synthorg"

    def test_binary_and_home_dirs_nest_under_state(self, tmp_path: Path) -> None:
        assert default_binary_dir(tmp_path) == tmp_path / "bin"
        assert default_devtunnels_home_dir(tmp_path) == tmp_path / "devtunnels-home"


class TestDownloadBinary:
    def test_downloads_atomically_into_target(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        requested = _install_fake_http(monkeypatch, b"binary-bytes")
        target = download_binary(
            url="https://example.invalid/tool-linux-amd64",
            target_dir=tmp_path / "bin",
            target_name="tool",
            binary_label="tool",
        )
        assert target == tmp_path / "bin" / "tool"
        assert target.read_bytes() == b"binary-bytes"
        assert requested == ["https://example.invalid/tool-linux-amd64"]
        if sys.platform != "win32":
            assert target.stat().st_mode & stat.S_IXUSR
        leftovers = [p for p in (tmp_path / "bin").iterdir() if p != target]
        assert leftovers == []

    def test_extract_failure_unlinks_tmp(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _install_fake_http(monkeypatch, b"archive-bytes")

        def _boom(_tmp: Path) -> Path:
            msg = "corrupt archive"
            raise TunnelError(msg)

        with pytest.raises(TunnelError, match="corrupt archive"):
            download_binary(
                url="https://example.invalid/tool.tgz",
                target_dir=tmp_path,
                target_name="tool",
                binary_label="tool",
                extract=_boom,
            )
        assert list(tmp_path.iterdir()) == []

    def test_extract_replaces_archive_with_member(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        archive = _tgz_bytes(tmp_path, "tool", b"exe-bytes")
        _install_fake_http(monkeypatch, archive.read_bytes())
        bin_dir = tmp_path / "bin"
        target = download_binary(
            url="https://example.invalid/tool.tgz",
            target_dir=bin_dir,
            target_name="tool",
            binary_label="tool",
            extract=partial(extract_tgz_member, member_name="tool", target_dir=bin_dir),
        )
        assert target.read_bytes() == b"exe-bytes"
        leftovers = [p for p in bin_dir.iterdir() if p != target]
        assert leftovers == []


class TestExtractTgzMember:
    def test_extracts_named_member(self, tmp_path: Path) -> None:
        archive = _tgz_bytes(tmp_path, "tool", b"exe-bytes")
        out = extract_tgz_member(archive, member_name="tool", target_dir=tmp_path)
        assert out.read_bytes() == b"exe-bytes"
        assert out.parent == tmp_path

    def test_missing_member_raises(self, tmp_path: Path) -> None:
        archive = _tgz_bytes(tmp_path, "other", b"exe-bytes")
        with pytest.raises(TunnelError, match="contained no binary"):
            extract_tgz_member(archive, member_name="tool", target_dir=tmp_path)


class TestExtractZipMember:
    def test_extracts_named_member(self, tmp_path: Path) -> None:
        archive = tmp_path / "asset.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("nested/devtunnel", b"exe-bytes")
        out = extract_zip_member(archive, member_name="devtunnel", target_dir=tmp_path)
        assert out.read_bytes() == b"exe-bytes"
        assert out.parent == tmp_path

    def test_missing_member_raises(self, tmp_path: Path) -> None:
        archive = tmp_path / "asset.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("nested/other", b"exe-bytes")
        with pytest.raises(TunnelError, match="contained no binary"):
            extract_zip_member(archive, member_name="devtunnel", target_dir=tmp_path)

    def test_hostile_member_path_stays_confined(self, tmp_path: Path) -> None:
        archive = tmp_path / "asset.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("../../evil/devtunnel", b"exe-bytes")
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        out = extract_zip_member(archive, member_name="devtunnel", target_dir=out_dir)
        assert out.parent == out_dir
        assert out.read_bytes() == b"exe-bytes"
