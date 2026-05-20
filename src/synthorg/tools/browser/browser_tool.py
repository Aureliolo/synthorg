"""Headless browser tool driving Playwright inside a DockerSandbox.

Single unified tool that dispatches on ``BrowserToolArgs.mode``. The
heavy lifting (Chromium launch, navigation, screenshot, axe-core scan)
happens inside the configured sandbox via :mod:`_executor`; the host
process computes the SSIM diff against the workspace-resident baseline.
"""

import asyncio
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Final, assert_never, cast

from pydantic import BaseModel  # noqa: TC002 -- ClassVar type at runtime

from synthorg.core.enums import ToolCategory
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.browser import (
    BROWSER_DIFF_FAILED,
    BROWSER_DIFF_START,
    BROWSER_DIFF_SUCCESS,
    BROWSER_NAVIGATE_FAILED,
    BROWSER_NAVIGATE_START,
    BROWSER_NAVIGATE_SUCCESS,
    BROWSER_SCREENSHOT_FAILED,
    BROWSER_SCREENSHOT_START,
    BROWSER_SCREENSHOT_SUCCESS,
    BROWSER_SPEC_FAILED,
    BROWSER_SPEC_START,
    BROWSER_SPEC_SUCCESS,
    BROWSER_START_COMMAND_FAILED,
    BROWSER_START_COMMAND_START,
    BROWSER_START_COMMAND_SUCCESS,
)
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.browser._args import BrowserToolArgs
from synthorg.tools.browser._baseline import WorkspaceBaselineStore
from synthorg.tools.browser._constants import (
    AXE_BUNDLE_PATH,
    AXE_VERSION_PIN,
    CONTAINER_WORKSPACE_ROOT,
    DEFAULT_VIEWPORT_HEIGHT,
    DEFAULT_VIEWPORT_WIDTH,
    DIFF_SSIM_TOLERANCE_DEFAULT,
    NAVIGATION_TIMEOUT_SECONDS,
    SCREENSHOTS_SUBDIR,
)
from synthorg.tools.browser._models import (
    A11yScanResult,
    A11yViolation,
    NavigationResult,
    ScreenshotDiffResult,
    ScreenshotMetadata,
    SpecResult,
)
from synthorg.tools.browser._ssim_differ import SSIMDiffer
from synthorg.tools.browser.errors import (
    BrowserAccessibilityError,
    BrowserArgumentError,
    BrowserBaselineNotFoundError,
    BrowserDiffError,
    BrowserDomainError,
    BrowserNavigationError,
    BrowserScreenshotError,
    BrowserStartCommandError,
)

if TYPE_CHECKING:
    from synthorg.tools.browser._protocols import ScreenshotDiffer
    from synthorg.tools.sandbox.protocol import SandboxBackend

logger = get_logger(__name__)

_EXECUTOR_SOURCE_PATH: Final[Path] = Path(__file__).resolve().parent / "_executor.py"
_DEPLOY_SUBDIR: Final[str] = ".synthorg/browser"
_EXECUTOR_DEPLOY_NAME: Final[str] = "executor.py"
_AXE_DEPLOY_NAME: Final[str] = "axe.min.js"


class BrowserTool(BaseTool):
    """Headless-browser automation backed by Playwright in a sandbox."""

    args_model: ClassVar[type[BaseModel] | None] = BrowserToolArgs

    def __init__(
        self,
        *,
        sandbox: SandboxBackend,
        workspace: Path,
        screenshot_differ: ScreenshotDiffer | None = None,
        owner_id: str = "browser-tool",
    ) -> None:
        """Wire the tool to a sandbox backend and the project workspace.

        Args:
            sandbox: Pluggable backend that runs the in-container
                executor (typically a DockerSandbox with the
                Playwright image).
            workspace: Persistent project workspace (#1974). Used to
                stage the executor + axe-core bundle and to read /
                write screenshot baselines.
            screenshot_differ: Optional override; defaults to
                :class:`SSIMDiffer`.
            owner_id: Sandbox lifecycle owner id (per-task scope).
        """
        super().__init__(
            name="browser",
            description=(
                "Headless browser via Playwright. Modes: navigate, "
                "screenshot, diff, accessibility_scan, spec. Captures "
                "screenshots to the project workspace; diffs against "
                "stored baselines via SSIM; injects axe-core for "
                "accessibility scans."
            ),
            category=ToolCategory.BROWSER,
            parameters_schema=BrowserToolArgs.model_json_schema(),
        )
        if not workspace.is_absolute():
            msg = f"workspace must be absolute, got {workspace!r}"
            raise BrowserDomainError(msg)
        self._sandbox = sandbox
        self._workspace = workspace.resolve()
        self._differ: ScreenshotDiffer = screenshot_differ or SSIMDiffer()
        self._baselines = WorkspaceBaselineStore(workspace=self._workspace)
        self._owner_id = owner_id

    async def execute(  # noqa: PLR0911 -- mode dispatch is the design
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Dispatch on ``mode`` and return a structured result."""
        try:
            args = BrowserToolArgs.model_validate(arguments)
        except Exception as exc:
            return _error_result(BrowserArgumentError, exc)

        try:
            self._ensure_deployed_assets()
            if args.start_command:
                await self._run_start_command(args)
            match args.mode:
                case "navigate":
                    return await self._mode_navigate(args)
                case "screenshot":
                    return await self._mode_screenshot(args)
                case "accessibility_scan":
                    return await self._mode_accessibility_scan(args)
                case "diff":
                    return await self._mode_diff(args)
                case "spec":
                    return await self._mode_spec(args)
                case _ as unhandled:
                    assert_never(unhandled)
        except BrowserDomainError as exc:
            return _error_result(type(exc), exc)

    async def cleanup(self) -> None:
        """Release sandbox resources tied to this tool's owner."""
        try:
            await self._sandbox.release_owner(self._owner_id)
        except Exception as exc:
            logger.warning(
                BROWSER_NAVIGATE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    # ---------------------------------------------------------------
    # Mode handlers
    # ---------------------------------------------------------------

    async def _mode_navigate(
        self,
        args: BrowserToolArgs,
    ) -> ToolExecutionResult:
        url = self._resolve_url(args)
        logger.debug(BROWSER_NAVIGATE_START, url=url)
        try:
            payload = await self._run_executor(
                operation="navigate",
                url=url,
                args=args,
            )
            navigation = self._build_navigation(payload, url)
        except BrowserDomainError as exc:
            logger.warning(
                BROWSER_NAVIGATE_FAILED,
                url=url,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        logger.debug(BROWSER_NAVIGATE_SUCCESS, url=navigation.final_url)
        return _ok_result(navigation)

    async def _mode_screenshot(
        self,
        args: BrowserToolArgs,
    ) -> ToolExecutionResult:
        url = self._resolve_url(args)
        if args.screenshot_name is None or args.spec_name is None:
            raise BrowserArgumentError(
                "screenshot mode requires spec_name and screenshot_name",
            )
        logger.debug(
            BROWSER_SCREENSHOT_START,
            url=url,
            spec=args.spec_name,
            screenshot=args.screenshot_name,
        )
        screenshot_host = self._baselines.current_path(
            spec_name=args.spec_name,
            screenshot_name=args.screenshot_name,
        )
        screenshot_container = self._to_container_path(screenshot_host)
        try:
            payload = await self._run_executor(
                operation="screenshot",
                url=url,
                args=args,
                screenshot_path=screenshot_container,
            )
            metadata = self._build_screenshot(payload, screenshot_host)
        except BrowserDomainError as exc:
            logger.warning(
                BROWSER_SCREENSHOT_FAILED,
                url=url,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        logger.debug(
            BROWSER_SCREENSHOT_SUCCESS,
            url=url,
            saved_path=metadata.saved_path,
        )
        return _ok_result(metadata)

    async def _mode_accessibility_scan(
        self,
        args: BrowserToolArgs,
    ) -> ToolExecutionResult:
        url = self._resolve_url(args)
        try:
            payload = await self._run_executor(
                operation="accessibility_scan",
                url=url,
                args=args,
            )
            a11y = self._build_a11y(payload, url, args)
        except BrowserDomainError:
            raise
        except Exception as exc:
            raise BrowserAccessibilityError(
                "Accessibility scan failed",
                context={"error_type": type(exc).__name__},
            ) from exc
        return _ok_result(a11y)

    async def _mode_diff(
        self,
        args: BrowserToolArgs,
    ) -> ToolExecutionResult:
        url = self._resolve_url(args)
        if args.spec_name is None or args.screenshot_name is None:
            raise BrowserArgumentError(
                "diff mode requires spec_name and screenshot_name",
            )
        screenshot_host = self._baselines.current_path(
            spec_name=args.spec_name,
            screenshot_name=args.screenshot_name,
        )
        screenshot_container = self._to_container_path(screenshot_host)
        await self._run_executor(
            operation="screenshot",
            url=url,
            args=args,
            screenshot_path=screenshot_container,
        )
        diff = await self._compute_diff(
            args=args,
            current_path=screenshot_host,
        )
        return _ok_result(diff)

    async def _mode_spec(
        self,
        args: BrowserToolArgs,
    ) -> ToolExecutionResult:
        url = self._resolve_url(args)
        if args.spec_name is None or args.screenshot_name is None:
            raise BrowserArgumentError(
                "spec mode requires spec_name and screenshot_name",
            )
        logger.debug(BROWSER_SPEC_START, spec=args.spec_name, url=url)
        screenshot_host = self._baselines.current_path(
            spec_name=args.spec_name,
            screenshot_name=args.screenshot_name,
        )
        screenshot_container = self._to_container_path(screenshot_host)
        try:
            payload = await self._run_executor(
                operation="capture",
                url=url,
                args=args,
                screenshot_path=screenshot_container,
            )
            navigation = self._build_navigation(payload, url)
            screenshot = self._build_screenshot(payload, screenshot_host)
            a11y = self._build_a11y(payload, url, args)
            diff = await self._compute_diff(
                args=args,
                current_path=screenshot_host,
            )
        except BrowserDomainError as exc:
            logger.warning(
                BROWSER_SPEC_FAILED,
                spec=args.spec_name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

        result = SpecResult(
            spec_name=args.spec_name,
            viewport_width=args.viewport_width or DEFAULT_VIEWPORT_WIDTH,
            viewport_height=args.viewport_height or DEFAULT_VIEWPORT_HEIGHT,
            navigation=navigation,
            screenshot=screenshot,
            diff=diff,
            accessibility=a11y,
            passed_all_checks=diff.passed_tolerance and a11y.passed,
        )
        logger.debug(
            BROWSER_SPEC_SUCCESS,
            spec=args.spec_name,
            passed=result.passed_all_checks,
            ssim=diff.ssim_score,
        )
        return _ok_result(result)

    # ---------------------------------------------------------------
    # Diff computation (host-side)
    # ---------------------------------------------------------------

    async def _compute_diff(
        self,
        *,
        args: BrowserToolArgs,
        current_path: Path,
    ) -> ScreenshotDiffResult:
        assert args.spec_name is not None  # noqa: S101 -- guarded by caller
        assert args.screenshot_name is not None  # noqa: S101 -- guarded by caller
        baseline_path = self._baselines.baseline_path(
            spec_name=args.spec_name,
            screenshot_name=args.screenshot_name,
        )
        tolerance = args.tolerance or DIFF_SSIM_TOLERANCE_DEFAULT
        logger.debug(
            BROWSER_DIFF_START,
            spec=args.spec_name,
            screenshot=args.screenshot_name,
            tolerance=tolerance,
        )

        if not baseline_path.exists():
            if not args.create_baseline_if_missing:
                logger.warning(
                    BROWSER_DIFF_FAILED,
                    spec=args.spec_name,
                    reason="baseline_missing",
                )
                raise BrowserBaselineNotFoundError(
                    "Baseline screenshot not found",
                    context={
                        "baseline_path": str(baseline_path),
                        "spec_name": args.spec_name,
                    },
                )
            adopted = self._baselines.adopt_current_as_baseline(
                spec_name=args.spec_name,
                screenshot_name=args.screenshot_name,
            )
            self._baselines.write_sidecar(
                spec_name=args.spec_name,
                screenshot_name=args.screenshot_name,
                png_bytes=adopted.read_bytes(),
            )
            logger.info(
                BROWSER_DIFF_SUCCESS,
                spec=args.spec_name,
                screenshot=args.screenshot_name,
                is_baseline_new=True,
            )
            return ScreenshotDiffResult(
                spec_name=args.spec_name,
                screenshot_name=args.screenshot_name,
                ssim_score=1.0,
                tolerance=tolerance,
                passed_tolerance=True,
                baseline_path=self._baselines.relative(adopted),
                current_path=self._baselines.relative(adopted),
                diff_image_path=None,
                is_baseline_new=True,
            )

        diff_output = self._baselines.diff_path(
            spec_name=args.spec_name,
            screenshot_name=args.screenshot_name,
        )
        try:
            score = await self._differ.compare(
                baseline=baseline_path,
                current=current_path,
                tolerance=tolerance,
                diff_output=diff_output,
            )
        except BrowserDiffError:
            raise
        except Exception as exc:
            raise BrowserDiffError(
                "Diff comparison failed",
                context={"error_type": type(exc).__name__},
            ) from exc

        passed = score >= tolerance
        result = ScreenshotDiffResult(
            spec_name=args.spec_name,
            screenshot_name=args.screenshot_name,
            ssim_score=score,
            tolerance=tolerance,
            passed_tolerance=passed,
            baseline_path=self._baselines.relative(baseline_path),
            current_path=self._baselines.relative(current_path),
            diff_image_path=self._baselines.relative(diff_output)
            if diff_output.exists()
            else None,
            is_baseline_new=False,
        )
        logger.debug(
            BROWSER_DIFF_SUCCESS,
            spec=args.spec_name,
            screenshot=args.screenshot_name,
            ssim=score,
            passed=passed,
        )
        return result

    # ---------------------------------------------------------------
    # Executor invocation
    # ---------------------------------------------------------------

    async def _run_executor(
        self,
        *,
        operation: str,
        url: str,
        args: BrowserToolArgs,
        screenshot_path: str | None = None,
    ) -> dict[str, Any]:
        deploy_dir_host = self._workspace / _DEPLOY_SUBDIR
        executor_container = (
            f"{CONTAINER_WORKSPACE_ROOT}/{_DEPLOY_SUBDIR}/{_EXECUTOR_DEPLOY_NAME}"
        )
        axe_container = (
            f"{CONTAINER_WORKSPACE_ROOT}/{_DEPLOY_SUBDIR}/{_AXE_DEPLOY_NAME}"
        )
        payload: dict[str, Any] = {
            "operation": operation,
            "url": url,
            "viewport_width": args.viewport_width or DEFAULT_VIEWPORT_WIDTH,
            "viewport_height": args.viewport_height or DEFAULT_VIEWPORT_HEIGHT,
            "full_page": args.full_page,
            "wait_condition": args.wait_condition,
            "navigation_timeout_seconds": (
                args.navigation_timeout_seconds or NAVIGATION_TIMEOUT_SECONDS
            ),
            "screenshot_path": screenshot_path,
            "axe_script_path": axe_container,
            "min_impact": args.min_impact,
            "axe_version": AXE_VERSION_PIN,
        }
        env = {"BROWSER_TOOL_ARGS_JSON": json.dumps(payload)}
        try:
            result = await self._sandbox.execute(
                command="python3",
                args=(executor_container,),
                env_overrides=env,
                owner_id=self._owner_id,
            )
        except Exception as exc:
            raise BrowserNavigationError(
                "Sandbox execution failed",
                context={
                    "operation": operation,
                    "error_type": type(exc).__name__,
                },
            ) from exc

        if result.timed_out:
            raise BrowserNavigationError(
                "Sandbox execution timed out",
                context={"operation": operation},
            )
        stdout = result.stdout or ""
        try:
            decoded = json.loads(stdout) if stdout else {}
        except json.JSONDecodeError as exc:
            raise BrowserDomainError(
                "Executor returned non-JSON output",
                context={
                    "operation": operation,
                    "stderr": (result.stderr or "")[:500],
                },
            ) from exc
        del deploy_dir_host

        if decoded.get("status") != "ok":
            err_type = decoded.get("error_type", "BrowserDomainError")
            message = decoded.get("message", "executor returned an error")
            raise _map_executor_error(err_type, message, operation)

        return cast("dict[str, Any]", decoded)

    async def _run_start_command(self, args: BrowserToolArgs) -> None:
        assert args.start_command is not None  # noqa: S101 -- guarded by caller
        logger.debug(
            BROWSER_START_COMMAND_START,
            command=args.start_command,
            timeout=args.start_command_timeout_seconds,
        )
        try:
            result = await self._sandbox.execute(
                command="bash",
                args=("-c", args.start_command),
                env_overrides=None,
                timeout=args.start_command_timeout_seconds,
                owner_id=self._owner_id,
            )
        except Exception as exc:
            logger.warning(
                BROWSER_START_COMMAND_FAILED,
                command=args.start_command,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise BrowserStartCommandError(
                "Failed to launch start_command",
                context={"error_type": type(exc).__name__},
            ) from exc
        if not result.success or result.timed_out:
            logger.warning(
                BROWSER_START_COMMAND_FAILED,
                command=args.start_command,
                returncode=result.returncode,
                timed_out=result.timed_out,
            )
            raise BrowserStartCommandError(
                "start_command exited with a non-zero status",
                context={
                    "returncode": result.returncode,
                    "timed_out": result.timed_out,
                },
            )
        logger.debug(
            BROWSER_START_COMMAND_SUCCESS,
            command=args.start_command,
        )
        await asyncio.sleep(0)  # cooperative yield

    # ---------------------------------------------------------------
    # Builders
    # ---------------------------------------------------------------

    def _build_navigation(
        self,
        payload: dict[str, Any],
        requested_url: str,
    ) -> NavigationResult:
        nav_payload = payload.get("navigation") or {}
        return NavigationResult(
            requested_url=requested_url,
            final_url=str(nav_payload.get("final_url", requested_url)),
            status_code=nav_payload.get("status_code"),
            duration_seconds=float(nav_payload.get("duration_seconds", 0.0)),
        )

    def _build_screenshot(
        self,
        payload: dict[str, Any],
        host_path: Path,
    ) -> ScreenshotMetadata:
        ss_payload = payload.get("screenshot") or {}
        if not ss_payload:
            raise BrowserScreenshotError(
                "Executor returned no screenshot payload",
            )
        sha = str(ss_payload.get("sha256", ""))
        if not sha:
            sha = ""
        return ScreenshotMetadata(
            saved_path=self._baselines.relative(host_path),
            width=int(ss_payload.get("width", DEFAULT_VIEWPORT_WIDTH)),
            height=int(ss_payload.get("height", DEFAULT_VIEWPORT_HEIGHT)),
            file_size_bytes=int(ss_payload.get("file_size_bytes", 0)),
            full_page=bool(ss_payload.get("full_page", False)),
            captured_at_iso=datetime.now(UTC).isoformat(),
            sha256=sha,
        )

    def _build_a11y(
        self,
        payload: dict[str, Any],
        url: str,
        args: BrowserToolArgs,
    ) -> A11yScanResult:
        a11y_payload = payload.get("accessibility") or {}
        if not a11y_payload:
            return A11yScanResult(
                url=url,
                min_impact=args.min_impact,
                violations=(),
                warnings=(),
                total_affected_nodes=0,
                scan_duration_seconds=0.0,
                axe_version=AXE_VERSION_PIN,
                passed=True,
            )
        violations = tuple(
            A11yViolation(**v) for v in a11y_payload.get("violations", [])
        )
        warnings = tuple(A11yViolation(**v) for v in a11y_payload.get("warnings", []))
        return A11yScanResult(
            url=str(a11y_payload.get("url", url)),
            min_impact=cast("Any", a11y_payload.get("min_impact", args.min_impact)),
            violations=violations,
            warnings=warnings,
            total_affected_nodes=int(
                a11y_payload.get("total_affected_nodes", 0),
            ),
            scan_duration_seconds=float(
                a11y_payload.get("scan_duration_seconds", 0.0),
            ),
            axe_version=str(a11y_payload.get("axe_version", AXE_VERSION_PIN)),
            passed=bool(a11y_payload.get("passed", True)),
        )

    # ---------------------------------------------------------------
    # Path translation + asset staging
    # ---------------------------------------------------------------

    def _resolve_url(self, args: BrowserToolArgs) -> str:
        if args.url:
            return args.url
        if args.path:
            container_rel = args.path.replace("\\", "/").lstrip("/")
            return f"file://{CONTAINER_WORKSPACE_ROOT}/{container_rel}"
        raise BrowserArgumentError(
            f"{args.mode!r} mode requires url or path",
        )

    def _to_container_path(self, host_path: Path) -> str:
        relative = host_path.resolve().relative_to(self._workspace).as_posix()
        return f"{CONTAINER_WORKSPACE_ROOT}/{relative}"

    def _ensure_deployed_assets(self) -> None:
        target_dir = self._workspace / _DEPLOY_SUBDIR
        target_dir.mkdir(parents=True, exist_ok=True)
        executor_target = target_dir / _EXECUTOR_DEPLOY_NAME
        if not executor_target.exists() or (
            executor_target.stat().st_mtime < _EXECUTOR_SOURCE_PATH.stat().st_mtime
        ):
            shutil.copyfile(_EXECUTOR_SOURCE_PATH, executor_target)
        axe_target = target_dir / _AXE_DEPLOY_NAME
        if not axe_target.exists() or (
            axe_target.stat().st_mtime < AXE_BUNDLE_PATH.stat().st_mtime
        ):
            shutil.copyfile(AXE_BUNDLE_PATH, axe_target)
        # Screenshots root is created lazily by the baseline store.
        screenshots_root = self._workspace / SCREENSHOTS_SUBDIR
        screenshots_root.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------


def _ok_result(model: BaseModel) -> ToolExecutionResult:
    payload = model.model_dump(mode="json")
    return ToolExecutionResult(
        content=json.dumps(payload),
        is_error=False,
        metadata=payload,
    )


def _error_result(
    error_cls: type[BrowserDomainError],
    exc: Exception,
) -> ToolExecutionResult:
    msg = safe_error_description(exc)
    return ToolExecutionResult(
        content=msg,
        is_error=True,
        metadata={
            "error_type": error_cls.__name__,
        },
    )


_EXECUTOR_ERROR_MAP: Final[dict[str, type[BrowserDomainError]]] = {
    "BrowserNavigationError": BrowserNavigationError,
    "BrowserScreenshotError": BrowserScreenshotError,
    "BrowserAccessibilityError": BrowserAccessibilityError,
    "BrowserDiffError": BrowserDiffError,
    "BrowserBaselineNotFoundError": BrowserBaselineNotFoundError,
    "BrowserStartCommandError": BrowserStartCommandError,
    "BrowserArgumentError": BrowserArgumentError,
    "TimeoutError": BrowserNavigationError,
    "PlaywrightTimeoutError": BrowserNavigationError,
    "FileNotFoundError": BrowserAccessibilityError,
}


def _map_executor_error(
    err_type: str,
    message: str,
    operation: str,
) -> BrowserDomainError:
    cls = _EXECUTOR_ERROR_MAP.get(err_type, BrowserDomainError)
    return cls(
        message,
        context={"operation": operation, "executor_error_type": err_type},
    )
