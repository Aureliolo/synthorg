# module-kind: adapter
"""Cloudflare quick-tunnel adapter (default tunnel provider).

Runs ``cloudflared tunnel --url http://127.0.0.1:<port>`` and scrapes
the ephemeral ``https://*.trycloudflare.com`` URL from its output.
Quick tunnels need no Cloudflare account or credential, which makes
this the safe default provider.

Binary resolution order: an operator-installed ``cloudflared`` on
``PATH``, then a previously downloaded copy under the shared tunnel
state dir's ``bin/``, then (when downloads are enabled) a fresh
download over HTTPS from the official Cloudflare GitHub release.
Operators who forbid runtime downloads set
``integrations.tunnel.cloudflared_download_enabled: false`` and
install the binary themselves.
"""

import asyncio
import contextlib
import platform
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.integrations.errors import (
    TunnelDownloadError,
    TunnelError,
    TunnelStartFailedError,
)
from synthorg.integrations.tunnel._binaries import (
    MACHINE_TO_ARCH,
    default_binary_dir,
    default_state_dir,
    download_binary,
    extract_tgz_member,
)
from synthorg.integrations.tunnel._process import (
    spawn_cli,
    spawn_drain_thread,
    terminate_process,
    wait_for_pattern,
)
from synthorg.integrations.tunnel.protocol import TunnelCredentialKind
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    CLOUDFLARE_TUNNEL_STARTED,
    TUNNEL_ALREADY_ACTIVE,
    TUNNEL_ERROR,
    TUNNEL_STOPPED,
)

logger = get_logger(__name__)

_URL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"https://[a-zA-Z0-9][a-zA-Z0-9-]*\.trycloudflare\.com"
)
_START_TIMEOUT_SECONDS: Final[float] = 60.0
_RELEASE_BASE_URL: Final[str] = (
    "https://github.com/cloudflare/cloudflared/releases/latest/download/"
)


def _release_asset_name() -> str | None:
    """Official release asset for this OS/architecture.

    Returns:
        The asset filename, or ``None`` when Cloudflare publishes no
        binary for this platform.
    """
    arch = MACHINE_TO_ARCH.get(platform.machine().lower())
    if arch is None:
        return None
    # ``platform.system()`` (not ``sys.platform``) so the non-host
    # branches stay type-checked rather than narrowed to unreachable.
    system = platform.system()
    if system == "Windows":
        return f"cloudflared-windows-{arch}.exe"
    if system == "Darwin":
        return f"cloudflared-darwin-{arch}.tgz"
    return f"cloudflared-linux-{arch}"


class CloudflareQuickTunnelAdapter:
    """Cloudflare quick-tunnel provider (accountless).

    Args:
        port: Local API port to expose.
        download_enabled: Whether a missing binary may be fetched from
            the official Cloudflare GitHub release at first start.
        binary_dir: Where downloaded binaries live (test seam).
    """

    def __init__(
        self,
        *,
        port: int,
        download_enabled: bool = True,
        binary_dir: Path | None = None,
    ) -> None:
        self._port = port
        self._download_enabled = download_enabled
        self._binary_dir = (
            binary_dir
            if binary_dir is not None
            else default_binary_dir(default_state_dir())
        )
        self._process: subprocess.Popen[bytes] | None = None
        self._public_url: str | None = None
        # Serialises start/stop; the adapter owns no background loop
        # beyond the child process, so the lock alone upholds the
        # single-tunnel invariant. Eager init: stop() must be safe
        # before start().
        self._lifecycle_lock = asyncio.Lock()  # lint-allow: loop-bound-init -- see.

    @property
    def provider_id(self) -> str:
        """Stable machine id (settings enum value)."""
        return "cloudflare"

    @property
    def display_name(self) -> str:
        """Human-readable provider name."""
        return "Cloudflare quick tunnel"

    @property
    def credential_kind(self) -> TunnelCredentialKind:
        """Quick tunnels are anonymous."""
        return TunnelCredentialKind.NONE

    async def availability(self) -> tuple[bool, str | None]:
        """Whether a cloudflared binary is present or fetchable.

        Returns:
            ``(available, detail)`` per the adapter contract.
        """
        if await asyncio.to_thread(self._locate_binary) is not None:
            return True, None
        asset = _release_asset_name()
        if asset is None:
            return False, "No official cloudflared build exists for this platform."
        if self._download_enabled:
            return True, "cloudflared will be downloaded on first start."
        return (
            False,
            (
                "cloudflared is not installed and automatic download is disabled;"
                " install it and ensure it is on PATH."
            ),
        )

    async def credential_configured(self) -> bool:
        """Quick tunnels need no credential.

        Returns:
            Always ``True``.
        """
        return True

    async def start(self) -> str:
        """Start the quick tunnel and return its public URL.

        Idempotent: an already-active tunnel returns its existing URL.

        Returns:
            The ``https://*.trycloudflare.com`` URL.

        Raises:
            TunnelError: When the binary cannot be resolved.
            TunnelStartFailedError: When the process dies early or no
                URL appears in time.
        """
        async with self._lifecycle_lock:
            if self._public_url is not None:
                logger.info(TUNNEL_ALREADY_ACTIVE, phase="start", port=self._port)
                return self._public_url
            binary = await self._ensure_binary()
            url = await self._spawn_and_capture_url(binary)
            self._public_url = url
            logger.info(
                CLOUDFLARE_TUNNEL_STARTED,
                public_url=url,
                port=self._port,
                note="tunnel exposes localhost publicly",
            )
            return url

    async def stop(self) -> None:
        """Stop the tunnel process (best-effort teardown)."""
        async with self._lifecycle_lock:
            process = self._process
            if process is None:
                return
            try:
                await terminate_process(process)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    TUNNEL_ERROR,
                    phase="disconnect",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
            # Drain threads exit on their own at pipe EOF.
            self._process = None
            self._public_url = None
            logger.info(TUNNEL_STOPPED)

    async def get_url(self) -> str | None:
        """Return the current public URL, or ``None`` if stopped.

        Checks the child process is still alive: a crashed vendor CLI
        must not keep reporting a dead URL as live indefinitely.
        """
        async with self._lifecycle_lock:
            process = self._process
            if process is not None and process.poll() is not None:
                logger.warning(
                    TUNNEL_ERROR,
                    phase="liveness",
                    returncode=process.returncode,
                    note="cloudflared exited; clearing tunnel state",
                )
                self._process = None
                self._public_url = None
            return self._public_url

    async def _spawn_and_capture_url(self, binary: Path) -> str:
        try:
            process = spawn_cli(
                [
                    str(binary),
                    "tunnel",
                    "--url",
                    f"http://127.0.0.1:{self._port}",
                    "--no-autoupdate",
                ]
            )
        except OSError as exc:
            logger.warning(
                TUNNEL_ERROR,
                phase="spawn",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"cloudflared failed to start: {safe_error_description(exc)}"
            raise TunnelStartFailedError(msg) from exc
        # cloudflared logs the assigned URL on stderr.
        if process.stderr is None or process.stdout is None:
            # Suppress teardown noise so the original failure propagates.
            with contextlib.suppress(OSError):
                await terminate_process(process)
            msg = "cloudflared subprocess pipes were not created"
            raise TunnelStartFailedError(msg)
        spawn_drain_thread(process.stdout, name="cloudflared-stdout")
        url = await wait_for_pattern(
            process.stderr,
            _URL_PATTERN,
            timeout_seconds=_START_TIMEOUT_SECONDS,
        )
        if url is None:
            with contextlib.suppress(OSError):
                await terminate_process(process)
            rc = process.returncode
            logger.warning(TUNNEL_ERROR, phase="start", returncode=rc)
            msg = (
                "cloudflared produced no quick-tunnel URL within "
                f"{_START_TIMEOUT_SECONDS:.0f}s (exit code {rc})"
            )
            raise TunnelStartFailedError(msg)
        self._process = process
        spawn_drain_thread(process.stderr, name="cloudflared-stderr")
        return url

    def _locate_binary(self) -> Path | None:
        found = shutil.which("cloudflared")
        if found:
            return Path(found)
        name = "cloudflared.exe" if sys.platform == "win32" else "cloudflared"
        candidate = self._binary_dir / name
        if candidate.is_file():
            return candidate
        return None

    async def _ensure_binary(self) -> Path:
        binary = await asyncio.to_thread(self._locate_binary)
        if binary is not None:
            return binary
        if not self._download_enabled:
            msg = (
                "cloudflared is not installed and automatic download is"
                " disabled; install it and ensure it is on PATH."
            )
            raise TunnelError(msg)
        asset = _release_asset_name()
        if asset is None:
            msg = "No official cloudflared build exists for this platform."
            raise TunnelError(msg)
        try:
            return await asyncio.to_thread(self._download_binary, asset)
        except TunnelError:
            raise
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                TUNNEL_ERROR,
                phase="download",
                asset=asset,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to download cloudflared: {safe_error_description(exc)}"
            raise TunnelDownloadError(msg) from exc

    def _download_binary(self, asset: str) -> Path:
        """Fetch the official release asset into the binary dir.

        Runs in a worker thread (blocking I/O).

        Returns:
            Path to the executable binary.
        """
        extract: Callable[[Path], Path] | None = None
        if asset.endswith(".tgz"):
            extract = partial(
                extract_tgz_member,
                member_name="cloudflared",
                target_dir=self._binary_dir,
            )
        target_name = "cloudflared.exe" if sys.platform == "win32" else "cloudflared"
        return download_binary(
            url=f"{_RELEASE_BASE_URL}{asset}",
            target_dir=self._binary_dir,
            target_name=target_name,
            binary_label="cloudflared",
            extract=extract,
        )
