# module-kind: code
"""Shared binary-download plumbing for CLI-backed tunnel adapters.

The Cloudflare and Dev Tunnels adapters both fetch their vendor CLI at
runtime when the operator has not installed one. This module holds the
pieces they share: the tunnel state-dir layout, the OS-arch table, the
atomic download-and-rename, and archive-member extraction.

There is deliberately no checksum verification: both vendors publish
assets at fixed HTTPS constants on their own CDNs and ship no detached
checksum verifiable without trusting the same origin, so TLS plus the
pinned URL is the integrity boundary. Revisit if either vendor starts
shipping independently-verifiable checksums or signatures. No user
input ever reaches the URL, so there is no SSRF surface.
"""

import contextlib
import os
import stat
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Final, NoReturn

import httpx

from synthorg.integrations.errors import TunnelDownloadError
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    TUNNEL_BINARY_DOWNLOADED,
    TUNNEL_ERROR,
)

logger = get_logger(__name__)

DOWNLOAD_TIMEOUT_SECONDS: Final[float] = 180.0
_EXECUTABLE_MODE: Final[int] = (
    stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH
)

MACHINE_TO_ARCH: Final[dict[str, str]] = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
    "armv7l": "arm",
    "i386": "386",
    "i686": "386",
}


def default_state_dir() -> Path:
    """Bare-metal tunnel state root.

    Returns:
        ``~/.synthorg`` (the same home-dir convention as
        ``~/.synthorg/config.yaml``).
    """
    return Path.home() / ".synthorg"


def default_binary_dir(state_dir: Path) -> Path:
    """Directory for tunnel binaries downloaded at runtime.

    Returns:
        ``<state_dir>/bin``.
    """
    return state_dir / "bin"


def default_devtunnels_home_dir(state_dir: Path) -> Path:
    """Private ``HOME`` that confines the devtunnel CLI's login cache.

    Returns:
        ``<state_dir>/devtunnels-home``.
    """
    return state_dir / "devtunnels-home"


def download_binary(
    *,
    url: str,
    target_dir: Path,
    target_name: str,
    binary_label: str,
    extract: Callable[[Path], Path] | None = None,
) -> Path:
    """Fetch a vendor CLI release asset into *target_dir*.

    Blocking I/O; call from a worker thread. Downloads to a temp file
    in the target directory and renames into place so a crashed
    download never leaves a half-written executable.

    Args:
        url: Fixed HTTPS asset URL (never user-influenced); its last
            path segment is logged as the asset name.
        target_dir: Directory the binary lands in (created if absent).
        target_name: Final filename inside *target_dir*.
        binary_label: Vendor CLI name for logging and temp prefixes.
        extract: Optional archive-member extractor applied to the
            downloaded temp file; returns the extracted binary path.

    Returns:
        Path to the executable binary.
    """
    asset = url.rstrip("/").rsplit("/", 1)[-1]
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / target_name
    with httpx.Client(
        timeout=DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True
    ) as client:
        response = client.get(url)
        response.raise_for_status()
        payload = response.content
    fd, tmp_name = tempfile.mkstemp(dir=target_dir, prefix=f".{binary_label}-")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        if extract is not None:
            extracted = extract(tmp_path)
            tmp_path.unlink(missing_ok=True)
            tmp_path = extracted
        if sys.platform != "win32":
            tmp_path.chmod(_EXECUTABLE_MODE)
        tmp_path.replace(target)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        raise
    logger.info(
        TUNNEL_BINARY_DOWNLOADED,
        binary=binary_label,
        asset=asset,
        path=str(target),
        size_bytes=len(payload),
    )
    return target


def _fail_extraction(*, operation: str, archive: Path, msg: str) -> NoReturn:
    """Log and raise a member-extraction failure with consistent structure.

    Raises:
        TunnelDownloadError: Always.
    """
    logger.warning(TUNNEL_ERROR, operation=operation, archive=str(archive), error=msg)
    raise TunnelDownloadError(msg)


def extract_tgz_member(archive: Path, *, member_name: str, target_dir: Path) -> Path:
    """Extract the named file member of a ``.tgz`` release asset.

    Returns:
        Path to the extracted file (inside *target_dir*).

    Raises:
        TunnelDownloadError: When the archive carries no such member.
    """
    with tarfile.open(archive, mode="r:gz") as tar:
        member = next(
            (m for m in tar.getmembers() if Path(m.name).name == member_name),
            None,
        )
        if member is None or not member.isfile():
            msg = f"{member_name} release archive contained no binary"
            _fail_extraction(operation="extract_tgz", archive=archive, msg=msg)
        extracted = tar.extractfile(member)
        if extracted is None:
            msg = f"{member_name} release archive member was unreadable"
            _fail_extraction(operation="extract_tgz", archive=archive, msg=msg)
        out = target_dir / f".{member_name}-extracted"
        out.write_bytes(extracted.read())
        return out


def extract_zip_member(archive: Path, *, member_name: str, target_dir: Path) -> Path:
    """Extract the named file member of a ``.zip`` release asset.

    The member's own archive path is discarded; the payload is written
    to a fixed name inside *target_dir*, so a hostile archive path can
    never escape it.

    Returns:
        Path to the extracted file (inside *target_dir*).

    Raises:
        TunnelDownloadError: When the archive carries no such member.
    """
    with zipfile.ZipFile(archive) as bundle:
        info = next(
            (
                i
                for i in bundle.infolist()
                if Path(i.filename).name == member_name and not i.is_dir()
            ),
            None,
        )
        if info is None:
            msg = f"{member_name} release archive contained no binary"
            _fail_extraction(operation="extract_zip", archive=archive, msg=msg)
        out = target_dir / f".{member_name}-extracted"
        with bundle.open(info) as handle:
            out.write_bytes(handle.read())
        return out
