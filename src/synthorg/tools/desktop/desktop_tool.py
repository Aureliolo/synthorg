"""Virtual desktop tool driving a headless X session in a DockerSandbox.

Single unified tool that dispatches on ``DesktopToolArgs.mode``. The
heavy lifting (Xvfb session bring-up, xdotool input, scrot capture)
happens inside the configured sandbox via :mod:`_executor`. The session
persists across calls in a warm per-agent container, so a GUI app
launched once can be driven and screenshotted by subsequent calls.

Caller contract for cleanup
---------------------------
:meth:`DesktopTool.execute` does NOT guarantee :meth:`cleanup` on its
own error path. Callers (worker / agent engine) MUST invoke
``await tool.cleanup()`` at the task boundary (or rely on the existing
``sandbox.release_owner`` hook). Otherwise the sandbox owner leaks for
the lifetime of the process.
"""

import asyncio
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Final, assert_never, cast
from uuid import uuid4

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from synthorg.api.boundary import parse_typed
from synthorg.core.enums import ToolCategory
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.desktop import (
    DESKTOP_ARGS_VALIDATION_FAILED,
    DESKTOP_ASSETS_DEPLOYED,
    DESKTOP_CLOSE_FAILED,
    DESKTOP_EXECUTOR_FAILED,
    DESKTOP_INPUT_START,
    DESKTOP_INPUT_SUCCESS,
    DESKTOP_LAUNCH_START,
    DESKTOP_LAUNCH_SUCCESS,
    DESKTOP_SCREENSHOT_START,
    DESKTOP_SCREENSHOT_SUCCESS,
)
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.desktop._args import DesktopToolArgs
from synthorg.tools.desktop._constants import (
    CONTAINER_WORKSPACE_ROOT,
    LAUNCH_TIMEOUT_SECONDS,
    OUTER_TIMEOUT_BUFFER_SECONDS,
    SCREENSHOT_TIMEOUT_SECONDS,
    SESSION_START_TIMEOUT_SECONDS,
)
from synthorg.tools.desktop._models import (
    InputResult,
    LaunchResult,
    ScreenshotResult,
)
from synthorg.tools.desktop._screenshot_store import WorkspaceScreenshotStore
from synthorg.tools.desktop._settings import DesktopSettings
from synthorg.tools.desktop.driver.factory import build_desktop_driver
from synthorg.tools.desktop.errors import (
    DesktopAppNotRunningError,
    DesktopArgumentError,
    DesktopDomainError,
    DesktopInputError,
    DesktopLaunchError,
    DesktopScreenshotError,
    DesktopSessionError,
)

if TYPE_CHECKING:
    from synthorg.tools.desktop.driver.protocol import DesktopDriver
    from synthorg.tools.sandbox.protocol import SandboxBackend

logger = get_logger(__name__)

_EXECUTOR_SOURCE_PATH: Final[Path] = Path(__file__).resolve().parent / "_executor.py"
_DEPLOY_SUBDIR: Final[str] = ".synthorg/desktop"
_EXECUTOR_DEPLOY_NAME: Final[str] = "executor.py"

# Per-workspace asset-deployment lock shared across DesktopTool instances.
_DEPLOY_LOCKS: Final[dict[Path, asyncio.Lock]] = {}


def _get_deploy_lock(workspace: Path) -> asyncio.Lock:
    """Return the workspace-scoped asset-deployment lock."""
    lock = _DEPLOY_LOCKS.get(workspace)
    if lock is None:
        lock = asyncio.Lock()
        _DEPLOY_LOCKS[workspace] = lock
    return lock


class DesktopTool(BaseTool):
    """Virtual-desktop automation backed by Xvfb + xdotool in a sandbox.

    Caller contract: invoke :meth:`cleanup` at the task boundary to
    release the sandbox owner. The default ``owner_id`` is unique per
    DesktopTool instance so concurrent agents do not share container
    lifecycle state.
    """

    args_model: ClassVar[type[BaseModel] | None] = DesktopToolArgs

    def __init__(
        self,
        *,
        sandbox: SandboxBackend,
        workspace: Path,
        driver: DesktopDriver | None = None,
        owner_id: str | None = None,
        settings: DesktopSettings | None = None,
    ) -> None:
        """Wire the tool to a sandbox backend and the project workspace.

        Args:
            sandbox: Pluggable backend that runs the in-container
                executor (typically a DockerSandbox with a desktop image).
            workspace: Persistent project workspace. Used to stage the
                executor and to write screenshots.
            driver: Optional :class:`DesktopDriver` override. When
                omitted it is built from ``settings.driver`` (default:
                the deterministic ``xvfb`` driver).
            owner_id: Sandbox lifecycle owner id. When omitted, each
                tool instance gets a uuid4-derived id.
            settings: Operator-resolved settings. When omitted the model
                defaults (mirroring the module constants) are used.
        """
        super().__init__(
            name="desktop",
            description=(
                "Virtual desktop automation. Launch a GUI app on a "
                "headless X session, then click / type / press keys / "
                "scroll and capture screenshots. Modes: launch, click, "
                "type, key, screenshot, scroll. SECURITY: the launch "
                "app_command runs via bash -c inside the sandbox; trust "
                "the sandbox boundary, never pass untrusted strings."
            ),
            category=ToolCategory.DESKTOP,
            parameters_schema=DesktopToolArgs.model_json_schema(),
        )
        if not workspace.is_absolute():
            msg = f"workspace must be absolute, got {workspace!r}"
            raise DesktopDomainError(msg)
        self._sandbox = sandbox
        self._workspace = workspace.resolve()
        self._settings = settings or DesktopSettings()
        self._driver: DesktopDriver = driver or build_desktop_driver(
            self._settings.driver,
        )
        self._screenshots = WorkspaceScreenshotStore(workspace=self._workspace)
        self._owner_id = owner_id or f"desktop-tool-{uuid4()}"

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Dispatch on ``mode`` and return a structured result."""
        try:
            args = parse_typed("tool.desktop", arguments, DesktopToolArgs)
        except PydanticValidationError as exc:
            logger.warning(
                DESKTOP_ARGS_VALIDATION_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return _error_result(DesktopArgumentError, exc)

        try:
            await self._ensure_deployed_assets()
            match args.mode:
                case "launch":
                    return await self._mode_launch(args)
                case "click" | "type" | "key" | "scroll":
                    return await self._mode_input(args)
                case "screenshot":
                    return await self._mode_screenshot(args)
                case _ as unhandled:
                    assert_never(unhandled)
        except DesktopDomainError as exc:
            return _error_result(type(exc), exc)

    async def cleanup(self) -> None:
        """Release sandbox resources tied to this tool's owner."""
        try:
            await self._sandbox.release_owner(self._owner_id)
        except Exception as exc:
            logger.warning(
                DESKTOP_CLOSE_FAILED,
                owner_id=self._owner_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    # ---------------------------------------------------------------
    # Mode handlers
    # ---------------------------------------------------------------

    async def _mode_launch(self, args: DesktopToolArgs) -> ToolExecutionResult:
        logger.debug(DESKTOP_LAUNCH_START, command_present=True)
        payload = await self._run_executor(
            operation="launch",
            args=args,
            extra={
                "app_command": args.app_command,
                "launch_timeout_seconds": args.launch_timeout_seconds,
            },
        )
        result = payload.get("result") or {}
        session = self._driver.session_config()
        launch = LaunchResult(
            display=str(result.get("display") or session.display),
            pid=int(result.get("pid") or 1),
            screen_width=int(result.get("screen_width") or session.screen_width),
            screen_height=int(result.get("screen_height") or session.screen_height),
        )
        logger.info(DESKTOP_LAUNCH_SUCCESS, pid=launch.pid, display=launch.display)
        return _ok_result(launch)

    async def _mode_input(self, args: DesktopToolArgs) -> ToolExecutionResult:
        logger.debug(DESKTOP_INPUT_START, action=args.mode)
        extra = {
            "x": args.x,
            "y": args.y,
            "button": args.button,
            "double": args.double,
            "text": args.text,
            "keys": args.keys,
            "direction": args.direction,
            "amount": args.amount,
        }
        payload = await self._run_executor(
            operation=args.mode,
            args=args,
            extra=extra,
        )
        result = payload.get("result") or {}
        input_result = InputResult(
            action=str(result.get("action") or args.mode),
            detail=str(result.get("detail") or ""),
        )
        logger.info(DESKTOP_INPUT_SUCCESS, action=input_result.action)
        return _ok_result(input_result)

    async def _mode_screenshot(self, args: DesktopToolArgs) -> ToolExecutionResult:
        assert args.screenshot_name is not None  # noqa: S101 -- args validator guard
        logger.debug(DESKTOP_SCREENSHOT_START, screenshot=args.screenshot_name)
        host_path = self._screenshots.screenshot_path(
            screenshot_name=args.screenshot_name,
        )
        container_path = self._container_path(host_path)
        payload = await self._run_executor(
            operation="screenshot",
            args=args,
            extra={"screenshot_path": container_path},
        )
        result = payload.get("result") or {}
        sha = str(result.get("sha256", ""))
        shot = ScreenshotResult(
            saved_path=self._screenshots.relative(host_path),
            width=int(result.get("width") or 1),
            height=int(result.get("height") or 1),
            file_size_bytes=int(result.get("file_size_bytes") or 0),
            captured_at_iso=datetime.now(UTC).isoformat(),
            sha256=sha,
        )
        logger.info(
            DESKTOP_SCREENSHOT_SUCCESS,
            screenshot=args.screenshot_name,
            sha256=shot.sha256,
        )
        return _ok_result(shot)

    # ---------------------------------------------------------------
    # Executor invocation
    # ---------------------------------------------------------------

    def _container_path(self, host_path: Path) -> str:
        """Map a host workspace path to its in-container equivalent."""
        relative = host_path.resolve().relative_to(self._workspace).as_posix()
        return f"{CONTAINER_WORKSPACE_ROOT}/{relative}"

    def _executor_timeout_seconds(self, *, operation: str) -> float:
        """Outer timeout for sandbox.execute covering the inner work."""
        budget = SESSION_START_TIMEOUT_SECONDS
        if operation == "launch":
            budget += LAUNCH_TIMEOUT_SECONDS
        if operation == "screenshot":
            budget += SCREENSHOT_TIMEOUT_SECONDS
        return budget + OUTER_TIMEOUT_BUFFER_SECONDS

    async def _run_executor(
        self,
        *,
        operation: str,
        args: DesktopToolArgs,
        extra: dict[str, Any],
    ) -> dict[str, Any]:
        executor_container = (
            f"{CONTAINER_WORKSPACE_ROOT}/{_DEPLOY_SUBDIR}/{_EXECUTOR_DEPLOY_NAME}"
        )
        payload: dict[str, Any] = {
            "operation": operation,
            "session": self._driver.session_config().model_dump(mode="json"),
            "settle_delay_seconds": args.settle_delay_seconds,
            **{k: v for k, v in extra.items() if v is not None},
        }
        env = {"DESKTOP_TOOL_ARGS_JSON": json.dumps(payload)}
        timeout = self._executor_timeout_seconds(operation=operation)
        try:
            result = await self._sandbox.execute(
                command="python3",
                args=(executor_container,),
                env_overrides=env,
                timeout=timeout,
                owner_id=self._owner_id,
            )
        except Exception as exc:
            logger.warning(
                DESKTOP_EXECUTOR_FAILED,
                operation=operation,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise DesktopSessionError(
                "Sandbox execution failed",
                context={"operation": operation, "error_type": type(exc).__name__},
            ) from exc

        if result.timed_out:
            logger.warning(
                DESKTOP_EXECUTOR_FAILED,
                operation=operation,
                reason="timeout",
                timeout=timeout,
            )
            raise DesktopSessionError(
                "Sandbox execution timed out",
                context={"operation": operation, "timeout": timeout},
            )
        stdout = result.stdout or ""
        try:
            decoded = json.loads(stdout) if stdout else {}
        except json.JSONDecodeError as exc:
            logger.warning(
                DESKTOP_EXECUTOR_FAILED,
                operation=operation,
                reason="non_json_output",
                error=safe_error_description(exc),
            )
            raise DesktopDomainError(
                "Executor returned non-JSON output",
                context={"operation": operation},
            ) from exc

        if decoded.get("status") != "ok":
            err_type = decoded.get("error_type", "DesktopDomainError")
            message = decoded.get("message", "executor returned an error")
            raise _map_executor_error(str(err_type), str(message), operation)
        return cast("dict[str, Any]", decoded)

    async def _ensure_deployed_assets(self) -> None:
        """Stage the executor script into the workspace (idempotent).

        Re-copies only when the source is newer than the deployed copy,
        so an updated executor lands on the next call without a stale
        in-container script lingering.
        """
        lock = _get_deploy_lock(self._workspace)
        dest_dir = self._workspace / _DEPLOY_SUBDIR
        dest = dest_dir / _EXECUTOR_DEPLOY_NAME
        source = _EXECUTOR_SOURCE_PATH

        def _deploy() -> bool:
            dest_dir.mkdir(parents=True, exist_ok=True)
            if dest.exists() and dest.stat().st_mtime >= source.stat().st_mtime:
                return False
            shutil.copyfile(source, dest)
            return True

        async with lock:
            try:
                changed = await asyncio.to_thread(_deploy)
            except OSError as exc:
                logger.warning(
                    DESKTOP_EXECUTOR_FAILED,
                    operation="deploy",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise DesktopSessionError(
                    "Failed to deploy desktop executor asset",
                    context={"error_type": type(exc).__name__},
                ) from exc
        if changed:
            logger.debug(DESKTOP_ASSETS_DEPLOYED, dest=str(dest))


def _ok_result(model: BaseModel) -> ToolExecutionResult:
    """Wrap a result model as a successful ToolExecutionResult."""
    payload = model.model_dump(mode="json")
    return ToolExecutionResult(
        content=json.dumps(payload),
        is_error=False,
        metadata=payload,
    )


def _error_result(
    error_cls: type[DesktopDomainError],
    exc: Exception,
) -> ToolExecutionResult:
    """Wrap a domain error as an error ToolExecutionResult."""
    return ToolExecutionResult(
        content=safe_error_description(exc),
        is_error=True,
        metadata={"error_type": error_cls.__name__},
    )


def _map_executor_error(
    err_type: str,
    message: str,
    operation: str,
) -> DesktopDomainError:
    """Map an executor error_type string to a host domain error."""
    if err_type == "DesktopAppNotRunningError":
        return DesktopAppNotRunningError(message)
    if err_type == "DesktopArgumentError":
        return DesktopArgumentError(message)
    match operation:
        case "launch":
            return DesktopLaunchError(message, context={"operation": operation})
        case "screenshot":
            return DesktopScreenshotError(message, context={"operation": operation})
        case "click" | "type" | "key" | "scroll":
            return DesktopInputError(message, context={"operation": operation})
        case _:
            return DesktopSessionError(message, context={"operation": operation})
