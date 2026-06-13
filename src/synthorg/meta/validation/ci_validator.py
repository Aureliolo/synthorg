"""Local CI validator for code modification proposals.

Runs ruff check, mypy, and pytest against changed files using
subprocess calls. Short-circuits on first failure to avoid
wasting time on later steps.
"""

import asyncio
import contextlib
from pathlib import Path
from typing import Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.meta.models import CIValidationResult
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meta import (
    META_CI_VALIDATION_FAILED,
    META_CI_VALIDATION_PASSED,
    META_CI_VALIDATION_STARTED,
)

logger = get_logger(__name__)

_DEFAULT_TIMEOUT_SECONDS: Final[int] = 300

_MAX_ERROR_OUTPUT_LENGTH: Final[int] = 2000


class LocalCIValidator:
    """Runs local CI checks (ruff, mypy, pytest) against changed files.

    Each step runs as an async subprocess. Steps short-circuit on
    failure: if lint fails, type-check and tests are skipped.

    Args:
        timeout_seconds: Maximum wall-clock time for each subprocess.
    """

    def __init__(
        self,
        *,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
        clock: Clock | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._clock = clock or SystemClock()

    async def validate(
        self,
        *,
        project_root: Path,
        changed_files: tuple[str, ...],
    ) -> CIValidationResult:
        """Run lint, type-check, and tests against changed files.

        Args:
            project_root: Absolute path to the project root.
            changed_files: Relative paths of files that changed.

        Returns:
            CI validation result with per-step outcomes.
        """
        logger.info(
            META_CI_VALIDATION_STARTED,
            file_count=len(changed_files),
        )
        start = self._clock.monotonic()
        errors: list[str] = []

        # Step 1: Lint.
        lint_ok = await self._run_lint(project_root, changed_files, errors)

        # Step 2: Type-check (skip if lint failed).
        typecheck_ok = False
        if lint_ok:
            typecheck_ok = await self._run_typecheck(
                project_root,
                changed_files,
                errors,
            )

        # Step 3: Tests (skip if earlier steps failed).
        tests_ok = False
        if lint_ok and typecheck_ok:
            tests_ok = await self._run_tests(
                project_root,
                changed_files,
                errors,
            )

        elapsed = self._clock.monotonic() - start
        passed = lint_ok and typecheck_ok and tests_ok

        if passed:
            logger.info(
                META_CI_VALIDATION_PASSED,
                duration_seconds=round(elapsed, 2),
            )
        else:
            logger.warning(
                META_CI_VALIDATION_FAILED,
                duration_seconds=round(elapsed, 2),
                error_count=len(errors),
            )

        return CIValidationResult(
            passed=passed,
            lint_passed=lint_ok,
            typecheck_passed=typecheck_ok,
            tests_passed=tests_ok,
            errors=tuple(errors),
            duration_seconds=elapsed,
        )

    async def _run_lint(
        self,
        project_root: Path,
        changed_files: tuple[str, ...],
        errors: list[str],
    ) -> bool:
        """Run ruff check on changed files.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        py_files = _existing_py_files(project_root, changed_files)
        if not py_files:
            return True
        cmd = ["uv", "run", "ruff", "check", *py_files]
        return await self._run_subprocess(
            cmd,
            project_root,
            "lint",
            errors,
        )

    async def _run_typecheck(
        self,
        project_root: Path,
        changed_files: tuple[str, ...],
        errors: list[str],
    ) -> bool:
        """Run mypy on changed files.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        py_files = _existing_py_files(project_root, changed_files)
        if not py_files:
            return True
        cmd = ["uv", "run", "mypy", *py_files]
        return await self._run_subprocess(
            cmd,
            project_root,
            "typecheck",
            errors,
        )

    async def _run_tests(
        self,
        project_root: Path,
        changed_files: tuple[str, ...],
        errors: list[str],
    ) -> bool:
        """Run pytest on test files related to changed source files.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        # Discover test files: for each src file, look for a
        # corresponding test file in the tests/ directory.
        test_files = _discover_test_files(project_root, changed_files)
        if not test_files:
            # Fail closed: generated code without matching tests must
            # not pass the CI gate silently.
            logger.warning(
                META_CI_VALIDATION_FAILED,
                reason="no_test_files_discovered",
                changed_file_count=len(changed_files),
            )
            errors.append(
                "tests: no matching test files discovered for changed files",
            )
            return False
        cmd = [
            "uv",
            "run",
            "python",
            "-m",
            "pytest",
            *test_files,
            "-m",
            "unit",
            "-x",
            "-q",
        ]
        return await self._run_subprocess(
            cmd,
            project_root,
            "tests",
            errors,
        )

    async def _run_subprocess(
        self,
        cmd: list[str],
        cwd: Path,
        step_name: str,
        errors: list[str],
    ) -> bool:
        """Run a subprocess and capture failure output.

        Args:
            cmd: Command and arguments.
            cwd: Working directory.
            step_name: Human-readable step name for error messages.
            errors: Mutable list to append error descriptions to.

        Returns:
            True if the subprocess exited with code 0.

        Raises:
            CancelledError: Raised on the corresponding failure path.
        """
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self._timeout,
            )
            return _check_returncode(
                proc,
                stdout,
                stderr,
                step_name,
                errors,
            )
        except TimeoutError:
            if proc is not None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
            logger.warning(
                META_CI_VALIDATION_FAILED,
                step=step_name,
                reason="timeout",
                timeout_seconds=self._timeout,
            )
            errors.append(
                f"{step_name}: timed out after {self._timeout}s",
            )
            return False
        except asyncio.CancelledError:
            if proc is not None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
            raise
        except FileNotFoundError:
            logger.warning(
                META_CI_VALIDATION_FAILED,
                step=step_name,
                reason="command_not_found",
                command=cmd[0],
            )
            errors.append(
                f"{step_name}: command not found: {cmd[0]}",
            )
            return False
        except OSError as exc:
            if proc is not None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
            logger.warning(
                META_CI_VALIDATION_FAILED,
                step=step_name,
                reason="subprocess_os_error",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            errors.append(
                f"{step_name}: subprocess error: {safe_error_description(exc)}"
            )
            return False


def _check_returncode(
    proc: asyncio.subprocess.Process,
    stdout: bytes,
    stderr: bytes,
    step_name: str,
    errors: list[str],
) -> bool:
    """Check subprocess exit code and capture errors.

    Args:
        proc: Completed subprocess.
        stdout: Captured stdout bytes.
        stderr: Captured stderr bytes.
        step_name: Human-readable step name for error messages.
        errors: Mutable list to append error descriptions to.

    Returns:
        True if the subprocess exited with code 0.
    """
    if proc.returncode != 0:
        output = (
            stdout.decode(errors="replace") + stderr.decode(errors="replace")
        ).strip()
        if len(output) > _MAX_ERROR_OUTPUT_LENGTH:
            output = output[:_MAX_ERROR_OUTPUT_LENGTH] + "... (truncated)"
        errors.append(f"{step_name}: {output}")
        return False
    return True


def _is_safe_ci_path(project_root: Path, rel: str) -> bool:
    """Reject a path that could inject a flag or escape the project root.

    ``changed_files`` originates from LLM-authored ``ImprovementProposal``
    output and is forwarded verbatim into the ruff / mypy / pytest argv. A
    value beginning with ``-`` would be parsed as an option (e.g.
    ``--plugin=evil`` loading an arbitrary pytest plugin from the host), and a
    path resolving outside ``project_root`` would reach arbitrary host files.

    Args:
        project_root: Absolute project root the path must stay within.
        rel: Candidate relative path.

    Returns:
        ``True`` only for a ``.py`` path with no leading dash or control
        characters that resolves inside ``project_root``.
    """
    if not rel.endswith(".py") or rel.startswith("-"):
        return False
    # Reject C0 controls (< space) and DEL (U+007F); both are non-printable
    # and have no legitimate place in a source-file path.
    if any(char < " " or char == "\x7f" for char in rel):
        return False
    try:
        resolved = (project_root / rel).resolve()
    except OSError, ValueError:
        return False
    return resolved.is_relative_to(project_root.resolve())


def _existing_py_files(
    project_root: Path,
    changed_files: tuple[str, ...],
) -> list[str]:
    """Filter changed files to existing, injection-safe Python files.

    Args:
        project_root: Absolute path to the project root.
        changed_files: Relative paths of changed files.

    Returns:
        Resolved absolute paths of changed .py files that exist on disk
        and pass :func:`_is_safe_ci_path`.
    """
    safe: list[str] = []
    for f in changed_files:
        if not (_is_safe_ci_path(project_root, f) and (project_root / f).exists()):
            continue
        # Forward the validated, fully-resolved absolute path so the
        # subprocess opens exactly the file the safety check approved,
        # closing the window where a symlink swapped in after validation
        # could redirect a relative name re-resolved at exec time (TOCTOU).
        safe.append(str((project_root / f).resolve()))
    return safe


def _discover_test_files(
    project_root: Path,
    changed_files: tuple[str, ...],
) -> list[str]:
    """Map changed source files to their test file paths.

    For each ``src/synthorg/meta/foo/bar.py``, looks for
    ``tests/unit/meta/test_bar.py`` or
    ``tests/unit/meta/foo/test_bar.py``.

    Args:
        project_root: Absolute path to the project root.
        changed_files: Relative paths of changed source files.

    Returns:
        List of test file paths that exist on disk.
    """
    test_files: list[str] = []
    seen: set[str] = set()
    for src in changed_files:
        parts = Path(src).parts
        if not parts or not parts[-1].endswith(".py"):
            continue
        stem = parts[-1]
        test_name = f"test_{stem}"
        # Try direct mapping under tests/unit/meta/.
        candidates = [
            str(Path("tests/unit/meta") / test_name),
        ]
        # Also try preserving subdirectory structure.
        if len(parts) > 4:  # noqa: PLR2004
            # e.g. src/synthorg/meta/strategies/foo.py
            # -> tests/unit/meta/strategies/test_foo.py
            sub = Path(*parts[3:-1])
            candidates.append(
                str(Path("tests/unit/meta") / sub / test_name),
            )
        for candidate in candidates:
            if candidate not in seen and _is_safe_ci_path(project_root, candidate):
                full = project_root / candidate
                if full.exists():
                    # Resolved absolute path, same TOCTOU rationale as
                    # ``_existing_py_files``.
                    test_files.append(str(full.resolve()))
                    seen.add(candidate)
    return test_files
