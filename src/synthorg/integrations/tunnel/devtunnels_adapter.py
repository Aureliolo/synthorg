# module-kind: adapter
"""GitHub Dev Tunnels adapter (Microsoft ``devtunnel`` CLI).

Drives the operator-installed ``devtunnel`` CLI: a GitHub device-code
login (`devtunnel user login -g -d`) establishes the credential the
CLI keeps in its own store, and ``devtunnel host`` exposes the local
API port on a ``*.devtunnels.ms`` URL. The CLI is proprietary and not
redistributable, so the adapter never downloads it; availability means
the binary is on ``PATH``.
"""

import asyncio
import re
import shutil
from asyncio.subprocess import Process
from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.integrations.errors import TunnelError
from synthorg.integrations.tunnel._process import (
    spawn_drain_task,
    stream_limit_bytes,
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
    "The devtunnel CLI is not installed; get it from"
    " https://aka.ms/TunnelsCliDownload and ensure it is on PATH."
)
_START_TIMEOUT_SECONDS: Final[float] = 60.0
_LOGIN_PROMPT_TIMEOUT_SECONDS: Final[float] = 30.0
_STATUS_TIMEOUT_SECONDS: Final[float] = 15.0

_HOST_URL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"https://[\w][\w.-]*\.devtunnels\.ms\S*"
)
_DEVICE_CODE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\bcode\s+([A-Z0-9][A-Z0-9-]{5,})\b", re.IGNORECASE
)
_VERIFICATION_URL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"https://\S*(?:login/device|devicelogin)\S*", re.IGNORECASE
)


class DevTunnelsAdapter:
    """GitHub Dev Tunnels provider via the ``devtunnel`` CLI.

    Args:
        port: Local API port to expose.
    """

    def __init__(self, *, port: int) -> None:
        self._port = port
        self._process: Process | None = None
        self._drain_tasks: list[asyncio.Task[None]] = []
        self._public_url: str | None = None
        self._login_task: asyncio.Task[None] | None = None
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
        return "GitHub Dev Tunnels"

    @property
    def credential_kind(self) -> TunnelCredentialKind:
        """Credential is a device-code login owned by the CLI."""
        return TunnelCredentialKind.DEVICE_LOGIN

    async def availability(self) -> tuple[bool, str | None]:
        """Whether the ``devtunnel`` CLI is on PATH.

        Returns:
            ``(available, detail)`` per the adapter contract.
        """
        binary = await asyncio.to_thread(shutil.which, _BINARY_NAME)
        if binary is None:
            return False, _INSTALL_HINT
        return True, None

    async def credential_configured(self) -> bool:
        """Whether the CLI reports a logged-in user.

        Returns:
            ``True`` when ``devtunnel user show`` reports a login.
        """
        binary = await asyncio.to_thread(shutil.which, _BINARY_NAME)
        if binary is None:
            return False
        try:
            process = await asyncio.create_subprocess_exec(
                binary,
                "user",
                "show",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            raw, _ = await asyncio.wait_for(
                process.communicate(), timeout=_STATUS_TIMEOUT_SECONDS
            )
        except TimeoutError:
            return False
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.debug(
                TUNNEL_ERROR,
                phase="credential_check",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return False
        text = raw.decode("utf-8", errors="replace").lower()
        return process.returncode == 0 and "not logged in" not in text

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
            TunnelError: When the CLI is missing or prints no usable
                device-code prompt.
        """
        binary = await asyncio.to_thread(shutil.which, _BINARY_NAME)
        if binary is None:
            raise TunnelError(_INSTALL_HINT)
        if self._login_task is not None and not self._login_task.done():
            msg = "A device login is already in progress; complete it first."
            raise TunnelError(msg)
        process = await asyncio.create_subprocess_exec(
            binary,
            "user",
            "login",
            "-g",
            "-d",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            limit=stream_limit_bytes(),
        )
        if process.stdout is None:
            await terminate_process(process)
            msg = "devtunnel subprocess pipe was not created"
            raise TunnelError(msg)
        prompt = await self._scrape_login_prompt(process)
        if prompt.already_logged_in:
            logger.info(DEVTUNNELS_LOGIN_COMPLETED, note="already logged in")
            return prompt
        logger.info(DEVTUNNELS_LOGIN_STARTED, verification_uri=prompt.verification_uri)
        self._login_task = asyncio.get_running_loop().create_task(
            self._await_login(process), name="devtunnels-login"
        )
        return prompt

    async def start(self) -> str:
        """Host the tunnel and return its public URL.

        Idempotent: an already-active tunnel returns its existing URL.

        Returns:
            The ``https://*.devtunnels.ms`` URL.

        Raises:
            TunnelError: When the CLI is missing, the login is absent,
                or no URL appears in time.
        """
        async with self._lifecycle_lock:
            if self._public_url is not None:
                logger.info(TUNNEL_ALREADY_ACTIVE, phase="start", port=self._port)
                return self._public_url
            binary = await asyncio.to_thread(shutil.which, _BINARY_NAME)
            if binary is None:
                raise TunnelError(_INSTALL_HINT)
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
            for task in self._drain_tasks:
                task.cancel()
            self._drain_tasks = []
            self._process = None
            self._public_url = None
            logger.info(TUNNEL_STOPPED)

    async def get_url(self) -> str | None:
        """Return the current public URL, or ``None`` if stopped."""
        return self._public_url

    async def _spawn_and_capture_url(self, binary: str) -> str:
        process = await asyncio.create_subprocess_exec(
            binary,
            "host",
            "-p",
            str(self._port),
            "--allow-anonymous",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=stream_limit_bytes(),
        )
        if process.stdout is None or process.stderr is None:
            await terminate_process(process)
            msg = "devtunnel subprocess pipes were not created"
            raise TunnelError(msg)
        stderr_drain = spawn_drain_task(process.stderr, name="devtunnel-stderr")
        url = await wait_for_pattern(
            process.stdout,
            _HOST_URL_PATTERN,
            timeout_seconds=_START_TIMEOUT_SECONDS,
        )
        if url is None:
            stderr_drain.cancel()
            await terminate_process(process)
            rc = process.returncode
            logger.warning(TUNNEL_ERROR, phase="start", returncode=rc)
            msg = (
                "devtunnel produced no tunnel URL within "
                f"{_START_TIMEOUT_SECONDS:.0f}s (exit code {rc})"
            )
            raise TunnelError(msg)
        self._process = process
        self._drain_tasks = [
            stderr_drain,
            spawn_drain_task(process.stdout, name="devtunnel-stdout"),
        ]
        return url

    async def _scrape_login_prompt(self, process: Process) -> DeviceLoginPrompt:
        """Read login output until the device-code prompt (or exit) appears.

        Returns:
            The scraped prompt; ``already_logged_in`` when the CLI
            exited cleanly without prompting.

        Raises:
            TunnelError: When the CLI exits non-zero or the prompt
                never appears.
        """
        stdout = process.stdout
        if stdout is None:  # pragma: no cover -- guarded by caller
            msg = "devtunnel subprocess pipe was not created"
            raise TunnelError(msg)
        verification_uri: str | None = None
        user_code: str | None = None
        deadline = asyncio.get_running_loop().time() + _LOGIN_PROMPT_TIMEOUT_SECONDS

        while verification_uri is None or user_code is None:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                line = await asyncio.wait_for(stdout.readline(), timeout=remaining)
            except TimeoutError:
                break
            if not line:
                await process.wait()
                if process.returncode == 0:
                    return DeviceLoginPrompt(already_logged_in=True)
                logger.warning(
                    TUNNEL_ERROR, phase="login", returncode=process.returncode
                )
                msg = f"devtunnel login failed (exit code {process.returncode})"
                raise TunnelError(msg)
            text = line.decode("utf-8", errors="replace")
            url_match = _VERIFICATION_URL_PATTERN.search(text)
            if url_match is not None:
                verification_uri = url_match.group(0).rstrip(".,;")
            code_match = _DEVICE_CODE_PATTERN.search(text)
            if code_match is not None:
                user_code = code_match.group(1)

        if verification_uri is None or user_code is None:
            await terminate_process(process)
            msg = "devtunnel printed no device-code prompt; try again."
            raise TunnelError(msg)
        return DeviceLoginPrompt(verification_uri=verification_uri, user_code=user_code)

    async def _await_login(self, process: Process) -> None:
        """Drain the login process to completion and log the outcome.

        Raises:
            asyncio.CancelledError: Propagated after terminating the
                child so a cancelled login never orphans the process.
        """
        stdout = process.stdout
        try:
            if stdout is not None:
                while await stdout.readline():
                    pass
            await process.wait()
        except asyncio.CancelledError:
            await terminate_process(process)
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                TUNNEL_ERROR,
                phase="login",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return
        if process.returncode == 0:
            logger.info(DEVTUNNELS_LOGIN_COMPLETED)
        else:
            logger.warning(TUNNEL_ERROR, phase="login", returncode=process.returncode)
