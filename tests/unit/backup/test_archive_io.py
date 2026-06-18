"""Unit tests for the pure tar/checksum/manifest I/O helpers.

These lock in the path-traversal and symlink-escape guards of
``extract_tar`` (the security-critical surface) plus the
checksum / compress / round-trip happy paths, which the service-level
tests exercise only indirectly.
"""

import io
import tarfile
from pathlib import Path

import pytest

from synthorg.backup._archive_io import (
    compress_dir,
    compute_checksum,
    extract_tar,
)
from synthorg.backup.errors import ManifestError

pytestmark = pytest.mark.unit


def _make_archive(path: Path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def _make_symlink_archive(path: Path, *, name: str, target: str) -> None:
    with tarfile.open(path, "w:gz") as tar:
        info = tarfile.TarInfo(name=name)
        info.type = tarfile.SYMTYPE
        info.linkname = target
        tar.addfile(info)


def test_compute_checksum_is_stable_and_excludes_manifest(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(b"alpha")
    (tmp_path / "b.txt").write_bytes(b"beta")
    (tmp_path / "manifest.json").write_bytes(b'{"ignored": true}')
    first = compute_checksum(tmp_path)
    # Re-running over the same content yields the same digest, and the
    # manifest is excluded (mutating it must not change the checksum).
    (tmp_path / "manifest.json").write_bytes(b'{"ignored": false}')
    assert compute_checksum(tmp_path) == first


def test_compress_then_extract_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "data.txt").write_bytes(b"payload")
    archive = tmp_path / "out.tar.gz"
    compress_dir(source, archive)

    target = tmp_path / "restored"
    extract_tar(archive, target)
    assert (target / "data.txt").read_bytes() == b"payload"


def test_extract_tar_rejects_absolute_member_path(tmp_path: Path) -> None:
    archive = tmp_path / "evil.tar.gz"
    _make_archive(archive, {"/etc/passwd": b"x"})
    with pytest.raises(ManifestError):
        extract_tar(archive, tmp_path / "restored")


def test_extract_tar_rejects_parent_traversal_member(tmp_path: Path) -> None:
    archive = tmp_path / "evil.tar.gz"
    _make_archive(archive, {"../escape.txt": b"x"})
    with pytest.raises(ManifestError):
        extract_tar(archive, tmp_path / "restored")


def test_extract_tar_rejects_absolute_symlink_target(tmp_path: Path) -> None:
    archive = tmp_path / "evil.tar.gz"
    _make_symlink_archive(archive, name="link", target="/etc/passwd")
    with pytest.raises(ManifestError):
        extract_tar(archive, tmp_path / "restored")


def test_extract_tar_rejects_parent_traversal_symlink_target(tmp_path: Path) -> None:
    archive = tmp_path / "evil.tar.gz"
    _make_symlink_archive(archive, name="link", target="../../secret")
    with pytest.raises(ManifestError):
        extract_tar(archive, tmp_path / "restored")
