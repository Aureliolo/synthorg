# module-kind: adapter
"""Dev Tunnels adapter (Microsoft ``devtunnel`` CLI).

Drives the ``devtunnel`` CLI: a GitHub device-code login
(``devtunnel user login -g -d``) establishes the credential the CLI
keeps in its own store, and ``devtunnel host`` exposes the local API
port on a ``*.devtunnels.ms`` URL.

Binary resolution order mirrors cloudflared: an operator-installed
``devtunnel`` on ``PATH``, then a previously downloaded copy under the
shared tunnel state dir's ``bin/``, then (when downloads are enabled)
a fresh download over HTTPS from Microsoft's fixed
``aka.ms/TunnelsCliDownload`` asset URLs. The assets sit at public
Microsoft URLs and are fetched at runtime by the operator's own
deployment; SynthOrg never redistributes the CLI itself. Operators who
forbid runtime downloads set
``integrations.tunnel.devtunnel_download_enabled: false`` and install
the binary themselves.

Microsoft offers no credential-injection API (every token-minting
command requires an already-logged-in CLI), so unlike ngrok's token in
the encrypted connection catalog the login cache is owned by the CLI
itself. On POSIX the adapter confines that cache by overriding
``HOME`` to a private owner-only directory under the tunnel state dir,
which puts the credential on persistent storage so it survives
container recreation. On Windows the login lives in the per-account
credential manager, not under ``%USERPROFILE%``, so there is nothing
to confine.

Subprocesses go through the ``_process`` helpers (``subprocess.Popen``
+ worker threads), never asyncio's subprocess API, so the adapter
works on the Windows ``SelectorEventLoop`` the API server pins.
"""

import asyncio
import contextlib
import platform
import re
import shutil
import stat
import subprocess
import sys
import threading
from functools import partial
from pathlib import Path
from typing import IO, Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.integrations.errors import (
    TunnelDownloadError,
    TunnelError,
    TunnelStartFailedError,
)
from synthorg.integrations.tunnel._binaries import (
    MACHINE_TO_ARCH,
    default_binary_dir,
    default_devtunnels_home_dir,
    default_state_dir,
    download_binary,
    extract_zip_member,
)
from synthorg.integrations.tunnel._process import (
    run_cli,
    spawn_cli,
    spawn_drain_thread,
    terminate_process,
    wait_for_pattern,
)
from synthorg.integrations.tunnel.protocol import (
    DeviceLoginPrompt,
    TunnelCredentialKind,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    DEVTUNNELS_LOGIN_COMPLETED,
    DEVTUNNELS_LOGIN_STARTED,
    DEVTUNNELS_TUNNEL_STARTED,
    TUNNEL_ALREADY_ACTIVE,
    TUNNEL_ERROR,
    TUNNEL_STOPPED,
)

logger = get_logger(__name__)

_BINARY_NAME: Final[str] = "devtunnel"
_INSTALL_HINT: Final[str] = (
    "The devtunnel CLI is not installed and automatic download is"
    " disabled; get it from https://aka.ms/TunnelsCliDownload and"
    " ensure it is on PATH."
)
_NO_BUILD_MSG: Final[str] = "No official devtunnel build exists for this platform."
_DOWNLOAD_BASE_URL: Final[str] = "https://aka.ms/TunnelsCliDownload/"
# Microsoft's fixed asset URL segments: bare binaries for Windows and
# Linux, zip archives for macOS. Segment names use x64/arm64, so the
# shared amd64/arm64 arch table maps onto them here.
_ASSET_SEGMENTS: Final[dict[tuple[str, str], tuple[str, str]]] = {
    ("Windows", "amd64"): ("win-x64", "binary"),
    ("Windows", "arm64"): ("win-arm64", "binary"),
    ("Linux", "amd64"): ("linux-x64", "binary"),
    ("Linux", "arm64"): ("linux-arm64", "binary"),
    ("Darwin", "amd64"): ("osx-x64-zip", "zip"),
    ("Darwin", "arm64"): ("osx-arm64-zip", "zip"),
}
_START_TIMEOUT_SECONDS: Final[float] = 60.0
_LOGIN_PROMPT_TIMEOUT_SECONDS: Final[float] = 30.0
_STATUS_TIMEOUT_SECONDS: Final[float] = 15.0

_HOST_URL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"https://[\w][\w.-]*\.devtunnels\.ms\S*"
)
# The CLI has shipped both "enter the code XXXX-XXXX" and
# "enter the code: XXXX-XXXX"; the optional colon accepts either.
_DEVICE_CODE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\bcode:?\s+([A-Z0-9][A-Z0-9-]{5,})\b", re.IGNORECASE
)
_VERIFICATION_URL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"https://\S*(?:login/device|devicelogin)\S*", re.IGNORECASE
)


def _asset_segment() -> tuple[str, str] | None:
    """Official download asset for this OS/architecture.

    Returns:
        ``(url_segment, kind)`` where kind is ``binary`` or ``zip``,
        or ``None`` when Microsoft publishes no build for this
        platform.
    """
    arch = MACHINE_TO_ARCH.get(platform.machine().lower())
    if arch is None:
        return None
    return _ASSET_SEGMENTS.get((platform.system(), arch))


class DevTunnelsAdapter:
    """Dev Tunnels provider via the ``devtunnel`` CLI (GitHub sign-in).

    Args:
        port: Local API port to expose.
        download_enabled: Whether a missing binary may be fetched from
            Microsoft's fixed asset URLs at first use.
        binary_dir: Where downloaded binaries live (test seam).
        home_dir: Private ``HOME`` confining the CLI's login cache on
            POSIX (test seam).
    """

    def __init__(
        self,
        *,
        port: int,
        download_enabled: bool = True,
        binary_dir: Path | None = None,
        home_dir: Path | None = None,
    ) -> None:
        self._port = port
        self._download_enabled = download_enabled
        self._binary_dir = (
            binary_dir
            if binary_dir is not None
            else default_binary_dir(default_state_dir())
        )
        self._home_dir = (
            home_dir
            if home_dir is not None
            else default_devtunnels_home_dir(default_state_dir())
        )
        self._process: subprocess.Popen[bytes] | None = None
        self._public_url: str | None = None
        self._login_process: subprocess.Popen[bytes] | None = None
        # Synchronous re-entrancy reservation for ``begin_login``: the
        # slot is taken before the first await (binary resolution can
        # download for 30s+), so a double-click cannot spawn two logins
        # against the same HOME.
        self._login_pending: bool = False
        # Serialises start/stop (single-tunnel invariant); the login
        # flow runs outside the lock because it never touches the
        # tunnel process. Eager init: stop() must be safe before start().
        self._lifecycle_lock = asyncio.Lock()  # lint-allow: loop-bound-init -- see.

    @property
    def provider_id(self) -> str:
        """Stable machine id (settings enum value)."""
        return "devtunnels"

    @property
    def display_name(self) -> str:
        """Human-readable provider name."""
        return "Dev Tunnels"

    @property
    def credential_kind(self) -> TunnelCredentialKind:
        """Credential is a device-code login owned by the CLI."""
        return TunnelCredentialKind.DEVICE_LOGIN

    async def availability(self) -> tuple[bool, str | None]:
        """Whether a devtunnel binary is present or fetchable.

        Returns:
            ``(available, detail)`` per the adapter contract.
        """
        if await asyncio.to_thread(self._locate_binary) is not None:
            return True, None
        if _asset_segment() is None:
            return False, _NO_BUILD_MSG
        if self._download_enabled:
            return True, "devtunnel will be downloaded on first start."
        return False, _INSTALL_HINT

    async def credential_configured(self) -> bool:
        """Whether the CLI reports a logged-in user.

        Never downloads: a read-only status check must not carry the
        download side effect, so a missing binary just means "no login".

        Returns:
            ``True`` when ``devtunnel user show`` reports a login.
        """
        binary = await asyncio.to_thread(self._locate_binary)
        if binary is None:
            return False
        try:
            result = await run_cli(
                [str(binary), "user", "show"],
                timeout_seconds=_STATUS_TIMEOUT_SECONDS,
                env=await self._confined_env(),
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            # WARNING (matching the manager's best-effort probes): a
            # broken binary must stay distinguishable from a clean
            # "not logged in".
            logger.warning(
                TUNNEL_ERROR,
                phase="credential_check",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return False
        if result is None:
            return False
        returncode, text = result
        return returncode == 0 and "not logged in" not in text.lower()

    async def begin_login(self) -> DeviceLoginPrompt:
        """Kick off the GitHub device-code login.

        Spawns ``devtunnel user login -g -d`` and scrapes the
        verification URL + one-time code from its output; the CLI keeps
        polling in the background and stores the credential itself once
        the operator completes the login in a browser.

        Returns:
            The login prompt (or ``already_logged_in=True`` when the
            CLI needed no fresh login).

        Raises:
            TunnelError: When a login is already in progress or the CLI
                cannot be resolved.
            TunnelStartFailedError: When the CLI fails to spawn or
                prints no usable device-code prompt.
        """
        active = self._login_process
        if self._login_pending or (active is not None and active.poll() is None):
            msg = "A device login is already in progress; complete it first."
            raise TunnelError(msg)
        self._login_pending = True
        try:
            binary = await self._ensure_binary()
            try:
                process = spawn_cli(
                    [str(binary), "user", "login", "-g", "-d"],
                    env=await self._confined_env(),
                )
            except OSError as exc:
                logger.warning(
                    TUNNEL_ERROR,
                    phase="login_spawn",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                msg = f"devtunnel failed to start: {safe_error_description(exc)}"
                raise TunnelStartFailedError(msg) from exc
            if process.stdout is None:
                # Suppress teardown noise so the original failure propagates.
                with contextlib.suppress(OSError):
                    await terminate_process(process)
                msg = "devtunnel subprocess pipe was not created"
                raise TunnelStartFailedError(msg)
            if process.stderr is not None:
                spawn_drain_thread(process.stderr, name="devtunnel-login-stderr")
            prompt = await self._scrape_login_prompt(process)
            if prompt.already_logged_in:
                logger.info(DEVTUNNELS_LOGIN_COMPLETED, note="already logged in")
                return prompt
            logger.info(
                DEVTUNNELS_LOGIN_STARTED, verification_uri=prompt.verification_uri
            )
            self._login_process = process
            self._watch_login(process)
            return prompt
        finally:
            self._login_pending = False

    async def start(self) -> str:
        """Host the tunnel and return its public URL.

        Idempotent: an already-active tunnel returns its existing URL.

        Returns:
            The ``https://*.devtunnels.ms`` URL.

        Raises:
            TunnelError: When the CLI cannot be resolved or the login
                is absent.
            TunnelStartFailedError: When no URL appears in time.
        """
        async with self._lifecycle_lock:
            if self._public_url is not None:
                logger.info(TUNNEL_ALREADY_ACTIVE, phase="start", port=self._port)
                return self._public_url
            binary = await self._ensure_binary()
            if not await self.credential_configured():
                msg = (
                    "Dev Tunnels requires a GitHub login; use Connect on the"
                    " tunnel card to sign in first."
                )
                raise TunnelError(msg)
            url = await self._spawn_and_capture_url(binary)
            self._public_url = url
            logger.info(
                DEVTUNNELS_TUNNEL_STARTED,
                public_url=url,
                port=self._port,
                note="tunnel exposes localhost publicly",
            )
            return url

    async def _terminate_login_process(self) -> None:
        """Terminate an in-flight device-login child (best-effort).

        The login flow runs outside ``_lifecycle_lock`` and its daemon drain
        thread blocks on the child, so without this a shutdown/restart mid
        device-login orphans the ``devtunnel login`` process (it polls the
        auth endpoint for minutes) and leaks its threads.
        """
        login = self._login_process
        self._login_process = None
        self._login_pending = False
        if login is None or login.poll() is not None:
            return
        try:
            await terminate_process(login)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                TUNNEL_ERROR,
                phase="disconnect",
                note="login_process_terminate_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def stop(self) -> None:
        """Stop the tunnel process + any in-flight device login (best-effort)."""
        await self._terminate_login_process()
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
                    note="devtunnel exited; clearing tunnel state",
                )
                self._process = None
                self._public_url = None
            return self._public_url

    async def _spawn_and_capture_url(self, binary: Path) -> str:
        try:
            process = spawn_cli(
                [str(binary), "host", "-p", str(self._port), "--allow-anonymous"],
                env=await self._confined_env(),
            )
        except OSError as exc:
            logger.warning(
                TUNNEL_ERROR,
                phase="spawn",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"devtunnel failed to start: {safe_error_description(exc)}"
            raise TunnelStartFailedError(msg) from exc
        if process.stdout is None or process.stderr is None:
            # Suppress teardown noise so the original failure propagates.
            with contextlib.suppress(OSError):
                await terminate_process(process)
            msg = "devtunnel subprocess pipes were not created"
            raise TunnelStartFailedError(msg)
        spawn_drain_thread(process.stderr, name="devtunnel-stderr")
        url = await wait_for_pattern(
            process.stdout,
            _HOST_URL_PATTERN,
            timeout_seconds=_START_TIMEOUT_SECONDS,
        )
        if url is None:
            with contextlib.suppress(OSError):
                await terminate_process(process)
            rc = process.returncode
            logger.warning(TUNNEL_ERROR, phase="start", returncode=rc)
            msg = (
                "devtunnel produced no tunnel URL within "
                f"{_START_TIMEOUT_SECONDS:.0f}s (exit code {rc})"
            )
            raise TunnelStartFailedError(msg)
        # The greedy URL pattern can capture a trailing separator the CLI
        # prints after the URL (e.g. a comma before "or via ..."); strip it so
        # the public URL copies/pastes cleanly (mirrors the verification-URL
        # path).
        url = url.rstrip(".,;")
        self._process = process
        spawn_drain_thread(process.stdout, name="devtunnel-stdout")
        return url

    def _prepare_home_dir(self) -> None:
        """Create the owner-only private HOME (blocking; call off-loop)."""
        self._home_dir.mkdir(parents=True, exist_ok=True)
        self._home_dir.chmod(stat.S_IRWXU)

    async def _confined_env(self) -> dict[str, str] | None:
        """Environment overrides confining the CLI's login cache.

        Returns:
            ``{"HOME": <private dir>}`` on POSIX (created owner-only so
            the token file is unreadable to other users), or ``None``
            on Windows, where the login lives in the per-account
            credential manager rather than under ``%USERPROFILE%``.
        """
        if sys.platform != "win32":
            # mkdir/chmod are blocking I/O; offload so a slow (e.g. network-
            # mounted) state dir cannot stall the event loop, matching the
            # rest of this adapter's blocking-I/O convention.
            await asyncio.to_thread(self._prepare_home_dir)
            return {"HOME": str(self._home_dir)}
        else:  # noqa: RET505 -- both platform branches must be if/else arms: mypy prunes a dead platform *branch* silently but flags trailing code as unreachable
            return None

    def _locate_binary(self) -> Path | None:
        found = shutil.which(_BINARY_NAME)
        if found:
            return Path(found)
        name = "devtunnel.exe" if sys.platform == "win32" else "devtunnel"
        candidate = self._binary_dir / name
        if candidate.is_file():
            return candidate
        return None

    async def _ensure_binary(self) -> Path:
        binary = await asyncio.to_thread(self._locate_binary)
        if binary is not None:
            return binary
        if not self._download_enabled:
            raise TunnelError(_INSTALL_HINT)
        asset = _asset_segment()
        if asset is None:
            raise TunnelError(_NO_BUILD_MSG)
        segment, kind = asset
        try:
            return await asyncio.to_thread(self._download_binary, segment, kind)
        except TunnelError:
            raise
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                TUNNEL_ERROR,
                phase="download",
                asset=segment,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to download devtunnel: {safe_error_description(exc)}"
            raise TunnelDownloadError(msg) from exc

    def _download_binary(self, segment: str, kind: str) -> Path:
        """Fetch the official asset into the binary dir.

        Runs in a worker thread (blocking I/O).

        Returns:
            Path to the executable binary.
        """
        extract = None
        if kind == "zip":
            extract = partial(
                extract_zip_member,
                member_name=_BINARY_NAME,
                target_dir=self._binary_dir,
            )
        target_name = "devtunnel.exe" if sys.platform == "win32" else "devtunnel"
        return download_binary(
            url=f"{_DOWNLOAD_BASE_URL}{segment}",
            target_dir=self._binary_dir,
            target_name=target_name,
            binary_label=_BINARY_NAME,
            extract=extract,
        )

    async def _scrape_login_prompt(
        self, process: subprocess.Popen[bytes]
    ) -> DeviceLoginPrompt:
        """Read login output until the device-code prompt (or exit) appears.

        Returns:
            The scraped prompt; ``already_logged_in`` when the CLI
            exited cleanly without prompting.

        Raises:
            TunnelError: When the CLI exits non-zero.
            TunnelStartFailedError: When the prompt never appears in
                time.
        """
        stdout = process.stdout
        if stdout is None:  # pragma: no cover -- guarded by caller
            msg = "devtunnel subprocess pipe was not created"
            raise TunnelError(msg)

        def _scan() -> DeviceLoginPrompt | int:
            """Blocking scan (worker thread).

            Returns:
                The scraped prompt, or the CLI's exit code at EOF.
            """
            verification_uri: str | None = None
            user_code: str | None = None
            for raw in iter(stdout.readline, b""):
                text = raw.decode("utf-8", errors="replace")
                url_match = _VERIFICATION_URL_PATTERN.search(text)
                if url_match is not None:
                    verification_uri = url_match.group(0).rstrip(".,;")
                code_match = _DEVICE_CODE_PATTERN.search(text)
                if code_match is not None:
                    user_code = code_match.group(1)
                if verification_uri is not None and user_code is not None:
                    return DeviceLoginPrompt(
                        verification_uri=verification_uri, user_code=user_code
                    )
            return process.wait()

        try:
            outcome = await asyncio.wait_for(
                asyncio.to_thread(_scan),
                timeout=_LOGIN_PROMPT_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            with contextlib.suppress(OSError):
                await terminate_process(process)
            msg = "devtunnel printed no device-code prompt; try again."
            raise TunnelStartFailedError(msg) from None
        if isinstance(outcome, DeviceLoginPrompt):
            return outcome
        if outcome == 0:
            return DeviceLoginPrompt(already_logged_in=True)
        logger.warning(TUNNEL_ERROR, phase="login", returncode=outcome)
        msg = f"devtunnel login failed (exit code {outcome})"
        raise TunnelError(msg)

    def _watch_login(self, process: subprocess.Popen[bytes]) -> None:
        """Drain the login process to completion and log the outcome."""
        stdout = process.stdout

        def _await_login() -> None:
            try:
                if stdout is not None:
                    self._drain_stream(stdout)
                returncode = process.wait()
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    TUNNEL_ERROR,
                    phase="login",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                return
            if returncode == 0:
                logger.info(DEVTUNNELS_LOGIN_COMPLETED)
            else:
                logger.warning(TUNNEL_ERROR, phase="login", returncode=returncode)

        threading.Thread(
            target=_await_login, name="devtunnels-login", daemon=True
        ).start()

    @staticmethod
    def _drain_stream(stream: IO[bytes]) -> None:
        for _ in iter(stream.readline, b""):
            pass
