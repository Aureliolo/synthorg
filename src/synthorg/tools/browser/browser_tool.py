# module-kind: complex_service
"""Headless browser tool driving Playwright inside a DockerSandbox.

Single unified tool that dispatches on ``BrowserToolArgs.mode``. The
heavy lifting (Chromium launch, navigation, screenshot, axe-core scan)
happens inside the configured sandbox via :mod:`_executor`; the host
process computes the SSIM diff against the workspace-resident baseline.

Caller contract for cleanup
---------------------------
:meth:`BrowserTool.execute` does NOT guarantee :meth:`cleanup` on its
own error path. Callers (worker / agent engine) MUST invoke
``await tool.cleanup()`` at the task boundary (or rely on the existing
``sandbox.release_owner`` hook fired by the worker's per-task release
mechanism). Otherwise the sandbox owner leaks for the lifetime of the
process.
"""

import asyncio
import hashlib
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    ClassVar,
    Final,
    TypedDict,
    assert_never,
    cast,
    override,
)
from uuid import uuid4

from pydantic import BaseModel, JsonValue
from pydantic import ValidationError as PydanticValidationError

from synthorg.api.boundary import parse_typed
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.enums import ToolCategory
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.browser import (
    BROWSER_ARGS_VALIDATION_FAILED,
    BROWSER_ASSETS_DEPLOYED,
    BROWSER_CLOSE_FAILED,
    BROWSER_DIFF_FAILED,
    BROWSER_DIFF_START,
    BROWSER_DIFF_SUCCESS,
    BROWSER_EXECUTOR_FAILED,
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
from synthorg.tools.browser._args import A11yImpact, BrowserToolArgs
from synthorg.tools.browser._baseline import WorkspaceBaselineStore
from synthorg.tools.browser._constants import (
    ACCESSIBILITY_SCAN_TIMEOUT_SECONDS,
    AXE_BUNDLE_PATH,
    AXE_VERSION_PIN,
    CONTAINER_WORKSPACE_ROOT,
    NAVIGATION_TIMEOUT_SECONDS,
    SCREENSHOT_TIMEOUT_SECONDS,
    SCREENSHOTS_SUBDIR,
    SHA256_HEX_LENGTH,
)
from synthorg.tools.browser._models import (
    A11yScanResult,
    A11yViolation,
    NavigationResult,
    ScreenshotDiffResult,
    ScreenshotMetadata,
    SpecResult,
)
from synthorg.tools.browser._settings import BrowserSettings
from synthorg.tools.browser._ssim_differ import SSIMDiffer
from synthorg.tools.browser.errors import (
    BrowserAccessibilityError,
    BrowserArgumentError,
    BrowserBaselineNotFoundError,
    BrowserDiffError,
    BrowserDomainError,
    BrowserLaunchError,
    BrowserNavigationError,
    BrowserScreenshotError,
    BrowserStartCommandError,
)
from synthorg.tools.network_validator import (
    extract_hostname,
    is_allowed_http_scheme,
    is_cloud_metadata_host,
)

if TYPE_CHECKING:
    from synthorg.tools.browser._protocols import ScreenshotDiffer
    from synthorg.tools.sandbox.protocol import SandboxBackend

logger = get_logger(__name__)

_EXECUTOR_SOURCE_PATH: Final[Path] = Path(__file__).resolve().parent / "_executor.py"
_DEPLOY_SUBDIR: Final[str] = ".synthorg/browser"
_EXECUTOR_DEPLOY_NAME: Final[str] = "executor.py"
_AXE_DEPLOY_NAME: Final[str] = "axe.min.js"
_OUTER_TIMEOUT_BUFFER_SECONDS: Final[float] = 30.0
_SHA256_HEX_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-f0-9]{64}$")

# Concurrency locks shared across BrowserTool instances. Two locks are
# needed because the natural race domains are different: assets are
# per-workspace, baselines are per-(spec, screenshot).
_DEPLOY_LOCKS: Final[dict[Path, asyncio.Lock]] = {}
_BASELINE_LOCKS: Final[dict[tuple[Path, str, str], asyncio.Lock]] = {}


class _NavPayload(TypedDict, total=False):
    """Navigation sub-payload decoded from the in-container executor."""

    requested_url: str
    final_url: str
    status_code: int | None
    duration_seconds: float


class _ScreenshotPayload(TypedDict, total=False):
    """Screenshot sub-payload decoded from the in-container executor."""

    saved_path: str
    width: int
    height: int
    file_size_bytes: int
    full_page: bool
    sha256: str


class _A11yPayload(TypedDict, total=False):
    """Accessibility sub-payload decoded from the in-container executor."""

    url: str
    min_impact: A11yImpact
    violations: list[dict[str, JsonValue]]
    warnings: list[dict[str, JsonValue]]
    total_affected_nodes: int
    scan_duration_seconds: float
    axe_version: str
    passed: bool


class _ExecutorResult(TypedDict, total=False):
    """Top-level JSON envelope returned by the in-container executor."""

    status: str
    error_type: str
    message: str
    navigation: _NavPayload
    screenshot: _ScreenshotPayload
    accessibility: _A11yPayload


def _get_deploy_lock(workspace: Path) -> asyncio.Lock:
    """Return the workspace-scoped asset-deployment lock.

    Returns:
        Result of type ``asyncio.Lock``.
    """
    lock = _DEPLOY_LOCKS.get(workspace)
    if lock is None:
        lock = asyncio.Lock()
        _DEPLOY_LOCKS[workspace] = lock
    return lock


def _get_baseline_lock(
    workspace: Path,
    spec_name: str,
    screenshot_name: str,
) -> asyncio.Lock:
    """Return the per-(spec, screenshot) baseline-adoption lock.

    Returns:
        Result of type ``asyncio.Lock``.
    """
    key = (workspace, spec_name, screenshot_name)
    lock = _BASELINE_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _BASELINE_LOCKS[key] = lock
    return lock


class BrowserTool(BaseTool):
    """Headless-browser automation backed by Playwright in a sandbox.

    Caller contract: invoke :meth:`cleanup` at the task boundary to
    release the sandbox owner. The default ``owner_id`` is unique per
    BrowserTool instance so concurrent agents do not share container
    lifecycle state.
    """

    args_model: ClassVar[type[BaseModel] | None] = BrowserToolArgs

    def __init__(
        self,
        *,
        sandbox: SandboxBackend,
        workspace: Path,
        screenshot_differ: ScreenshotDiffer | None = None,
        owner_id: str | None = None,
        settings: BrowserSettings | None = None,
    ) -> None:
        """Wire the tool to a sandbox backend and the project workspace.

        Args:
            sandbox: Pluggable backend that runs the in-container
                executor (typically a DockerSandbox with the
                Playwright image).
            workspace: Persistent project workspace. Used to stage the
                executor + axe-core bundle and to read / write
                screenshot baselines.
            screenshot_differ: Optional override; defaults to
                :class:`SSIMDiffer`. Implementations are stateless
                and safe for concurrent use across tasks.
            owner_id: Sandbox lifecycle owner id. When omitted, each
                tool instance gets a uuid4-derived id so concurrent
                tools do not collide on a shared sandbox owner.
            settings: Operator-resolved settings. When omitted, the
                model defaults (mirroring the module constants) are
                used; the factory passes a populated value when the
                ``ConfigResolver`` chain resolves overrides.

        Raises:
            BrowserDomainError: If the related operation fails.
        """
        super().__init__(
            name="browser",
            description=(
                "Headless browser via Playwright. Modes: navigate, "
                "screenshot, diff, accessibility_scan, spec. Captures "
                "screenshots to the project workspace; diffs against "
                "stored baselines via SSIM; injects axe-core for "
                "accessibility scans. SECURITY: the optional "
                "start_command argument is passed to bash -c inside "
                "the sandbox; trust the sandbox boundary, never pass "
                "untrusted strings."
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
        self._owner_id = owner_id or f"browser-tool-{uuid4()}"
        self._settings = settings or BrowserSettings()

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Dispatch on ``mode`` and return a structured result.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        try:
            args = parse_typed("tool.browser", arguments, BrowserToolArgs)
        except PydanticValidationError as exc:
            logger.warning(
                BROWSER_ARGS_VALIDATION_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return _error_result(BrowserArgumentError, exc)

        try:
            await self._ensure_deployed_assets()
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
            reraise_critical(exc)
            logger.warning(
                BROWSER_CLOSE_FAILED,
                owner_id=self._owner_id,
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
        """Mode navigate.

        Returns:
            Result of type ``ToolExecutionResult``.

        Raises:
            BrowserDomainError: If the related operation fails.
        """
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
        """Mode screenshot.

        Returns:
            Result of type ``ToolExecutionResult``.

        Raises:
            BrowserArgumentError: If the related operation fails.
            BrowserDomainError: If the related operation fails.
        """
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
        # Serialise the capture step under the same per-(spec, screenshot)
        # lock used for baseline promotion. Two concurrent screenshot
        # captures would otherwise race on ``current.png`` and one task
        # could publish the other task's bytes through metadata.
        lock = _get_baseline_lock(
            self._workspace,
            args.spec_name,
            args.screenshot_name,
        )
        async with lock:
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
        """Mode accessibility scan.

        Returns:
            Result of type ``ToolExecutionResult``.

        Raises:
            BrowserDomainError: If the related operation fails.
            BrowserAccessibilityError: If the related operation fails.
        """
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
            reraise_critical(exc)
            logger.warning(
                BROWSER_EXECUTOR_FAILED,
                operation="accessibility_scan",
                url=url,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise BrowserAccessibilityError(
                "Accessibility scan failed",
                context={"error_type": type(exc).__name__},
            ) from exc
        return _ok_result(a11y)

    async def _mode_diff(
        self,
        args: BrowserToolArgs,
    ) -> ToolExecutionResult:
        """Mode diff.

        Returns:
            Result of type ``ToolExecutionResult``.

        Raises:
            BrowserArgumentError: If the related operation fails.
        """
        url = self._resolve_url(args)
        if args.spec_name is None or args.screenshot_name is None:
            raise BrowserArgumentError(
                "diff mode requires spec_name and screenshot_name",
            )
        # Lock the capture→diff sequence so a concurrent task cannot
        # overwrite ``current.png`` between capture and SSIM compare.
        lock = _get_baseline_lock(
            self._workspace,
            args.spec_name,
            args.screenshot_name,
        )
        async with lock:
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
        """Mode spec.

        Returns:
            Result of type ``ToolExecutionResult``.

        Raises:
            BrowserArgumentError: If the related operation fails.
            BrowserDomainError: If the related operation fails.
        """
        url = self._resolve_url(args)
        if args.spec_name is None or args.screenshot_name is None:
            raise BrowserArgumentError(
                "spec mode requires spec_name and screenshot_name",
            )
        logger.debug(BROWSER_SPEC_START, spec=args.spec_name, url=url)
        # Same per-key lock as screenshot / diff modes -- spec stitches
        # capture + diff together and must not race a concurrent task
        # using the same (spec, screenshot) slot.
        lock = _get_baseline_lock(
            self._workspace,
            args.spec_name,
            args.screenshot_name,
        )
        async with lock:
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
            viewport_width=args.viewport_width or self._settings.viewport_width,
            viewport_height=args.viewport_height or self._settings.viewport_height,
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
        """Compute diff.

        Returns:
            Result of type ``ScreenshotDiffResult``.

        Raises:
            BrowserDiffError: If the related operation fails.
        """
        assert args.spec_name is not None  # noqa: S101 -- guarded by caller
        assert args.screenshot_name is not None  # noqa: S101 -- guarded by caller
        baseline_path = self._baselines.baseline_path(
            spec_name=args.spec_name,
            screenshot_name=args.screenshot_name,
        )
        tolerance = args.tolerance or self._settings.diff_ssim_tolerance
        logger.debug(
            BROWSER_DIFF_START,
            spec=args.spec_name,
            screenshot=args.screenshot_name,
            tolerance=tolerance,
        )

        if not baseline_path.exists():
            return await self._handle_missing_baseline(
                args=args,
                baseline_path=baseline_path,
                tolerance=tolerance,
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
            reraise_critical(exc)
            logger.warning(
                BROWSER_DIFF_FAILED,
                spec=args.spec_name,
                screenshot=args.screenshot_name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
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

    async def _handle_missing_baseline(
        self,
        *,
        args: BrowserToolArgs,
        baseline_path: Path,
        tolerance: float,
    ) -> ScreenshotDiffResult:
        """Either promote current to baseline, or raise NotFound.

        Callers MUST hold the per-(spec, screenshot) baseline lock for
        the full capture→diff window. This method assumes the lock is
        already held so it does not re-acquire (asyncio.Lock is not
        reentrant; re-acquiring would deadlock).

        Returns:
            Result of type ``ScreenshotDiffResult``.

        Raises:
            BrowserBaselineNotFoundError: If the requested resource cannot be located.
            BrowserDiffError: If the related operation fails.
        """
        assert args.spec_name is not None  # noqa: S101 -- guarded by caller
        assert args.screenshot_name is not None  # noqa: S101 -- guarded by caller

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

        if baseline_path.exists():
            # The outer per-key lock excludes other tasks in this
            # process, so reaching this branch implies a baseline was
            # promoted out-of-band (e.g. by a sibling process sharing
            # the workspace). Forcing a retry is safer than returning
            # a fabricated ``ssim_score=1.0`` pass: the agent should
            # re-enter the compare path against the now-present
            # baseline rather than treating a missed comparison as
            # success.
            raise BrowserDiffError(
                "Baseline was created by a concurrent writer; retry the diff",
                context={
                    "spec_name": args.spec_name,
                    "screenshot_name": args.screenshot_name,
                    "baseline_path": str(baseline_path),
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
            chromium_image=self._settings.image_pin,
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

    # ---------------------------------------------------------------
    # Executor invocation
    # ---------------------------------------------------------------

    def _build_executor_payload(
        self,
        *,
        operation: str,
        url: str,
        args: BrowserToolArgs,
        screenshot_path: str | None,
        axe_container: str,
    ) -> dict[str, JsonValue]:
        """Assemble the JSON payload sent to the in-container executor.

        Returns:
            Mapping from ``str`` to ``JsonValue``.
        """
        return {
            "operation": operation,
            "url": url,
            "viewport_width": (args.viewport_width or self._settings.viewport_width),
            "viewport_height": (args.viewport_height or self._settings.viewport_height),
            "full_page": args.full_page,
            "wait_condition": args.wait_condition,
            "navigation_timeout_seconds": (
                args.navigation_timeout_seconds or NAVIGATION_TIMEOUT_SECONDS
            ),
            "launch_timeout_seconds": self._settings.launch_timeout_seconds,
            "screenshot_path": screenshot_path,
            "axe_script_path": axe_container,
            "min_impact": args.min_impact,
            "axe_version": AXE_VERSION_PIN,
        }

    def _executor_timeout_seconds(
        self,
        *,
        operation: str,
        args: BrowserToolArgs,
    ) -> float:
        """Outer timeout for sandbox.execute covering inner Playwright budgets.

        Returns:
            Result of type ``float``.
        """
        nav = args.navigation_timeout_seconds or NAVIGATION_TIMEOUT_SECONDS
        budget = self._settings.launch_timeout_seconds + nav
        if operation in {"screenshot", "capture"}:
            budget += SCREENSHOT_TIMEOUT_SECONDS
        if operation in {"accessibility_scan", "capture"}:
            budget += ACCESSIBILITY_SCAN_TIMEOUT_SECONDS
        return budget + _OUTER_TIMEOUT_BUFFER_SECONDS

    async def _run_executor(
        self,
        *,
        operation: str,
        url: str,
        args: BrowserToolArgs,
        screenshot_path: str | None = None,
    ) -> _ExecutorResult:
        """Run executor.

        Returns:
            The decoded executor result envelope.

        Raises:
            BrowserLaunchError: If the related operation fails.
            _map_executor_error: Raised when the relevant invariant fails.
            BrowserDomainError: If the related operation fails.
        """
        executor_container = (
            f"{CONTAINER_WORKSPACE_ROOT}/{_DEPLOY_SUBDIR}/{_EXECUTOR_DEPLOY_NAME}"
        )
        axe_container = (
            f"{CONTAINER_WORKSPACE_ROOT}/{_DEPLOY_SUBDIR}/{_AXE_DEPLOY_NAME}"
        )
        payload = self._build_executor_payload(
            operation=operation,
            url=url,
            args=args,
            screenshot_path=screenshot_path,
            axe_container=axe_container,
        )
        env = {"BROWSER_TOOL_ARGS_JSON": json.dumps(payload)}
        timeout = self._executor_timeout_seconds(operation=operation, args=args)
        try:
            result = await self._sandbox.execute(
                command="python3",
                args=(executor_container,),
                env_overrides=env,
                timeout=timeout,
                owner_id=self._owner_id,
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                BROWSER_EXECUTOR_FAILED,
                operation=operation,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            # Sandbox bootstrap / launch failures belong to the launch
            # taxonomy regardless of which mode triggered the call --
            # the agent's remediation differs from a navigation error
            # (retry vs. reconfigure provider).
            raise BrowserLaunchError(
                "Sandbox execution failed",
                context={
                    "operation": operation,
                    "error_type": type(exc).__name__,
                },
            ) from exc

        if result.timed_out:
            logger.warning(
                BROWSER_EXECUTOR_FAILED,
                operation=operation,
                reason="timeout",
                timeout=timeout,
            )
            raise BrowserLaunchError(
                "Sandbox execution timed out",
                context={"operation": operation, "timeout": timeout},
            )
        stdout = result.stdout or ""
        try:
            decoded = json.loads(stdout) if stdout else {}
        except json.JSONDecodeError as exc:
            logger.warning(
                BROWSER_EXECUTOR_FAILED,
                operation=operation,
                reason="non_json_output",
                error=safe_error_description(exc),
            )
            raise BrowserDomainError(
                "Executor returned non-JSON output",
                context={
                    "operation": operation,
                    "stderr": (result.stderr or "")[:500],
                },
            ) from exc

        if not isinstance(decoded, dict):
            logger.warning(
                BROWSER_EXECUTOR_FAILED,
                operation=operation,
                reason="non_object_output",
            )
            raise BrowserDomainError(
                "Executor returned a non-object JSON payload",
                context={"operation": operation},
            )
        if decoded.get("status") != "ok":
            err_type = decoded.get("error_type", "BrowserDomainError")
            message = decoded.get("message", "executor returned an error")
            raise _map_executor_error(err_type, str(message), operation)

        return cast("_ExecutorResult", decoded)

    async def _run_start_command(self, args: BrowserToolArgs) -> None:
        """Run start command.

        Raises:
            BrowserStartCommandError: If the related operation fails.
        """
        assert args.start_command is not None  # noqa: S101 -- guarded by caller
        logger.debug(
            BROWSER_START_COMMAND_START,
            command_present=True,
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
            reraise_critical(exc)
            logger.warning(
                BROWSER_START_COMMAND_FAILED,
                command_present=True,
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
                command_present=True,
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
        logger.debug(BROWSER_START_COMMAND_SUCCESS, command_present=True)

    # ---------------------------------------------------------------
    # Builders
    # ---------------------------------------------------------------

    def _build_navigation(
        self,
        payload: _ExecutorResult,
        requested_url: str,
    ) -> NavigationResult:
        """Build navigation.

        Returns:
            Result of type ``NavigationResult``.
        """
        nav_payload: _NavPayload = payload.get("navigation") or {}
        return NavigationResult(
            requested_url=requested_url,
            final_url=str(nav_payload.get("final_url", requested_url)),
            status_code=nav_payload.get("status_code"),
            duration_seconds=float(nav_payload.get("duration_seconds", 0.0)),
        )

    def _build_screenshot(
        self,
        payload: _ExecutorResult,
        host_path: Path,
    ) -> ScreenshotMetadata:
        """Build screenshot.

        Returns:
            Result of type ``ScreenshotMetadata``.

        Raises:
            BrowserScreenshotError: If the related operation fails.
        """
        ss_payload: _ScreenshotPayload = payload.get("screenshot") or {}
        if not ss_payload:
            raise BrowserScreenshotError(
                "Executor returned no screenshot payload",
            )
        sha = str(ss_payload.get("sha256", ""))
        if len(sha) != SHA256_HEX_LENGTH or _SHA256_HEX_PATTERN.match(sha) is None:
            raise BrowserScreenshotError(
                "Executor returned an invalid sha256",
                context={"sha256_length": len(sha)},
            )
        return ScreenshotMetadata(
            saved_path=self._baselines.relative(host_path),
            width=int(
                ss_payload.get("width", self._settings.viewport_width),
            ),
            height=int(
                ss_payload.get("height", self._settings.viewport_height),
            ),
            file_size_bytes=int(ss_payload.get("file_size_bytes", 0)),
            full_page=bool(ss_payload.get("full_page", False)),
            captured_at_iso=datetime.now(UTC).isoformat(),
            sha256=sha,
        )

    def _build_a11y(
        self,
        payload: _ExecutorResult,
        url: str,
        args: BrowserToolArgs,
    ) -> A11yScanResult:
        """Build a11y.

        Returns:
            Result of type ``A11yScanResult``.
        """
        a11y_payload: _A11yPayload = payload.get("accessibility") or {}
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
            A11yViolation.model_validate(v) for v in a11y_payload.get("violations", [])
        )
        warnings = tuple(
            A11yViolation.model_validate(v) for v in a11y_payload.get("warnings", [])
        )
        return A11yScanResult(
            url=str(a11y_payload.get("url", url)),
            min_impact=a11y_payload.get("min_impact", args.min_impact),
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
        """Resolve url.

        Returns:
            Result of type ``str``.

        Raises:
            BrowserArgumentError: If the related operation fails.
        """
        if args.url:
            # The url field is for navigating to a web target only:
            # restrict it to http/https so a caller cannot smuggle a
            # local-file, data, javascript, or leading-dash flag-injection
            # scheme through it and escape the workspace. Local files go
            # through the path field below, which is workspace-scoped and
            # traversal-checked. Loopback and private targets stay allowed:
            # the browser drives the app-under-test inside the sandbox.
            if not is_allowed_http_scheme(args.url):
                raise BrowserArgumentError(
                    "url must use http:// or https:// (use the 'path' "
                    "field for workspace-relative local files)",
                    context={"url": args.url},
                )
            # Block link-local / cloud-metadata endpoints
            # (169.254.169.254, metadata.google.internal, fe80::) which
            # are never a legitimate app-under-test target but are
            # reachable inside a sandbox with host-network access.
            # Loopback and private ranges stay allowed: the app under
            # test runs on localhost or a docker-network address.
            host = extract_hostname(args.url)
            if host is not None and is_cloud_metadata_host(host):
                raise BrowserArgumentError(
                    "url must not target a link-local or cloud-metadata endpoint",
                    context={"url": args.url},
                )
            return args.url
        if args.path:
            normalised = args.path.replace("\\", "/")
            self._reject_path_traversal(normalised)
            container_rel = normalised.lstrip("/")
            return f"file://{CONTAINER_WORKSPACE_ROOT}/{container_rel}"
        raise BrowserArgumentError(
            f"{args.mode!r} mode requires url or path",
        )

    @staticmethod
    def _reject_path_traversal(path: str) -> None:
        """Reject `..` segments and absolute paths in the workspace-relative path.

        Raises:
            BrowserArgumentError: If the related operation fails.
        """
        if path.startswith("/"):
            raise BrowserArgumentError(
                "path must be workspace-relative, not absolute",
                context={"path": path},
            )
        segments = path.split("/")
        if any(segment == ".." for segment in segments):
            raise BrowserArgumentError(
                "path must not contain '..' segments",
                context={"path": path},
            )

    def _to_container_path(self, host_path: Path) -> str:
        """To container path.

        Returns:
            Result of type ``str``.
        """
        relative = host_path.resolve().relative_to(self._workspace).as_posix()
        return f"{CONTAINER_WORKSPACE_ROOT}/{relative}"

    async def _ensure_deployed_assets(self) -> None:
        """Ensure deployed assets."""
        lock = _get_deploy_lock(self._workspace)
        async with lock:
            target_dir = self._workspace / _DEPLOY_SUBDIR
            target_dir.mkdir(parents=True, exist_ok=True)
            executor_target = target_dir / _EXECUTOR_DEPLOY_NAME
            executor_changed = self._copy_if_stale(
                _EXECUTOR_SOURCE_PATH,
                executor_target,
            )
            axe_target = target_dir / _AXE_DEPLOY_NAME
            axe_changed = self._copy_if_stale(AXE_BUNDLE_PATH, axe_target)
            screenshots_root = self._workspace / SCREENSHOTS_SUBDIR
            screenshots_root.mkdir(parents=True, exist_ok=True)
        if executor_changed or axe_changed:
            logger.debug(
                BROWSER_ASSETS_DEPLOYED,
                executor=str(executor_target),
                axe=str(axe_target),
                executor_changed=executor_changed,
                axe_changed=axe_changed,
            )

    @staticmethod
    def _copy_if_stale(source: Path, target: Path) -> bool:
        """Copy if stale.

        Returns:
            ``True`` if the operation succeeds, ``False`` otherwise.
        """
        if not target.exists() or (target.stat().st_mtime < source.stat().st_mtime):
            shutil.copyfile(source, target)
            return True
        return False


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------


def _ok_result(model: BaseModel) -> ToolExecutionResult:
    """Ok result.

    Returns:
        Result of type ``ToolExecutionResult``.
    """
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
    """Error result.

    Returns:
        Result of type ``ToolExecutionResult``.
    """
    msg = safe_error_description(exc)
    return ToolExecutionResult(
        content=msg,
        is_error=True,
        metadata={"error_type": error_cls.__name__},
    )


_EXECUTOR_ERROR_MAP: Final[dict[str, type[BrowserDomainError]]] = {
    "BrowserNavigationError": BrowserNavigationError,
    "BrowserLaunchError": BrowserLaunchError,
    "BrowserScreenshotError": BrowserScreenshotError,
    "BrowserAccessibilityError": BrowserAccessibilityError,
    "BrowserDiffError": BrowserDiffError,
    "BrowserBaselineNotFoundError": BrowserBaselineNotFoundError,
    "BrowserStartCommandError": BrowserStartCommandError,
    "BrowserArgumentError": BrowserArgumentError,
    # ``asyncio.wait_for`` raises TimeoutError when the executor's
    # launch budget is exceeded; navigation timeouts come back as
    # PlaywrightTimeoutError from page.goto.
    "TimeoutError": BrowserLaunchError,
    "PlaywrightTimeoutError": BrowserNavigationError,
    "FileNotFoundError": BrowserAccessibilityError,
}


def _map_executor_error(
    err_type: str,
    message: str,
    operation: str,
) -> BrowserDomainError:
    """Map executor error.

    Returns:
        Result of type ``BrowserDomainError``.
    """
    cls = _EXECUTOR_ERROR_MAP.get(err_type, BrowserDomainError)
    return cls(
        message,
        context={"operation": operation, "executor_error_type": err_type},
    )


# A small constant to keep `hashlib.sha256` reachable via this module (used
# by historical helpers; retained for stable import paths in tests).
_ = hashlib.sha256
