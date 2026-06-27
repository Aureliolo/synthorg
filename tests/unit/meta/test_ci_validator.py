"""Unit tests for local CI validator."""

from collections.abc import Sequence
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synthorg.meta.errors import CIValidatorHostExecutionError
from synthorg.meta.validation.ci_validator import (
    LocalCIValidator,
    _discover_test_files,
    _existing_py_files,
    _is_safe_ci_path,
)
from synthorg.meta.validation.scope_validator import ScopeValidator
from synthorg.tools.sandbox.protocol import SandboxBackend
from synthorg.tools.sandbox.result import SandboxResult

pytestmark = pytest.mark.unit

# Permissive envelope for tests that exercise the safety / discovery logic
# independently of scope gating; scope-specific behaviour has dedicated tests.
_ALLOW_ALL = ScopeValidator(allowed_paths=("*",), forbidden_paths=())


def _sandbox_result(
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
) -> SandboxResult:
    return SandboxResult(
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
        timed_out=timed_out,
    )


def _make_sandbox(
    results: Sequence[SandboxResult] | SandboxResult | None = None,
    *,
    backend_type: str = "docker",
    execute_side_effect: object = None,
) -> MagicMock:
    """Build a mock container ``SandboxBackend`` for the validator."""
    sandbox = MagicMock(spec=SandboxBackend)
    sandbox.get_backend_type = MagicMock(return_value=backend_type)
    if execute_side_effect is not None:
        sandbox.execute = AsyncMock(side_effect=execute_side_effect)
    elif isinstance(results, SandboxResult):
        sandbox.execute = AsyncMock(return_value=results)
    elif results is not None:
        sandbox.execute = AsyncMock(side_effect=list(results))
    else:
        sandbox.execute = AsyncMock(return_value=_sandbox_result())
    return sandbox


def _make_validator(
    *,
    timeout_seconds: int = 10,
    scope_validator: ScopeValidator = _ALLOW_ALL,
    sandbox: MagicMock | None = None,
) -> LocalCIValidator:
    """Build a validator rooted at the (absolute) current directory."""
    return LocalCIValidator(
        project_root=Path.cwd(),
        scope_validator=scope_validator,
        sandbox=sandbox if sandbox is not None else _make_sandbox(),
        timeout_seconds=timeout_seconds,
    )


class TestCiPathSafety:
    def test_accepts_plain_relative_py_path(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "mod.py").write_text("", encoding="utf-8")
        assert _is_safe_ci_path(tmp_path, "src/mod.py") is True

    def test_rejects_leading_dash_flag_injection(self, tmp_path: Path) -> None:
        assert _is_safe_ci_path(tmp_path, "--plugin=evil.py") is False

    def test_rejects_path_escaping_project_root(self, tmp_path: Path) -> None:
        assert _is_safe_ci_path(tmp_path, "../outside.py") is False

    def test_rejects_non_python_and_control_chars(self, tmp_path: Path) -> None:
        assert _is_safe_ci_path(tmp_path, "src/mod.txt") is False
        assert _is_safe_ci_path(tmp_path, "src/mo\nd.py") is False

    def test_existing_py_files_drops_flag_injection(self, tmp_path: Path) -> None:
        (tmp_path / "real.py").write_text("", encoding="utf-8")
        result = _existing_py_files(
            tmp_path, ("real.py", "--plugin=evil.py"), _ALLOW_ALL
        )
        # The validated file is forwarded as a resolved absolute path.
        assert result == [str((tmp_path / "real.py").resolve())]

    def test_existing_py_files_drops_out_of_scope(self, tmp_path: Path) -> None:
        """A safe, existing file outside the modification envelope is excluded."""
        (tmp_path / "in_scope.py").write_text("", encoding="utf-8")
        (tmp_path / "out_of_scope.py").write_text("", encoding="utf-8")
        scope = ScopeValidator(allowed_paths=("in_scope.py",), forbidden_paths=())
        result = _existing_py_files(tmp_path, ("in_scope.py", "out_of_scope.py"), scope)
        assert result == [str((tmp_path / "in_scope.py").resolve())]

    def test_rejects_del_control_char(self, tmp_path: Path) -> None:
        assert _is_safe_ci_path(tmp_path, "src/mo\x7fd.py") is False


_FAKE_FILES = ("src/synthorg/meta/strategies/new.py",)
_FAKE_TESTS = ["tests/unit/meta/test_new.py"]

# Common patches for tests that exercise the subprocess pipeline.
# These bypass file-existence checks since we use fake paths.
_BYPASS_FILE_CHECK = patch(
    "synthorg.meta.validation.ci_validator._existing_py_files",
    return_value=list(_FAKE_FILES),
)
_BYPASS_TEST_DISCOVERY = patch(
    "synthorg.meta.validation.ci_validator._discover_test_files",
    return_value=list(_FAKE_TESTS),
)


class TestLocalCIValidator:
    """LocalCIValidator tests."""

    def test_init_rejects_relative_project_root(self) -> None:
        with pytest.raises(ValueError, match="absolute"):
            LocalCIValidator(
                project_root=Path("relative/root"),
                scope_validator=_ALLOW_ALL,
                sandbox=_make_sandbox(),
            )

    def test_init_rejects_host_subprocess_backend(self) -> None:
        """A non-container (host) backend is refused (fail closed)."""
        with pytest.raises(CIValidatorHostExecutionError):
            LocalCIValidator(
                project_root=Path.cwd(),
                scope_validator=_ALLOW_ALL,
                sandbox=_make_sandbox(backend_type="subprocess"),
            )

    async def test_all_steps_pass(self) -> None:
        validator = _make_validator(sandbox=_make_sandbox(_sandbox_result()))
        with _BYPASS_FILE_CHECK, _BYPASS_TEST_DISCOVERY:
            result = await validator.validate(changed_files=_FAKE_FILES)
        assert result.passed
        assert result.lint_passed
        assert result.typecheck_passed
        assert result.tests_passed
        assert result.errors == ()
        assert result.duration_seconds >= 0.0

    async def test_lint_failure_short_circuits(self) -> None:
        sandbox = _make_sandbox(
            _sandbox_result(returncode=1, stdout="E501 line too long")
        )
        validator = _make_validator(sandbox=sandbox)
        with _BYPASS_FILE_CHECK, _BYPASS_TEST_DISCOVERY:
            result = await validator.validate(changed_files=_FAKE_FILES)
        assert not result.passed
        assert not result.lint_passed
        assert not result.typecheck_passed
        assert not result.tests_passed
        assert len(result.errors) == 1
        assert "lint" in result.errors[0]
        # Only lint ran (short-circuit): a single sandbox.execute call.
        assert sandbox.execute.await_count == 1

    async def test_typecheck_failure_skips_tests(self) -> None:
        sandbox = _make_sandbox(
            [
                _sandbox_result(returncode=0),
                _sandbox_result(returncode=1, stderr="error: incompatible types"),
            ]
        )
        validator = _make_validator(sandbox=sandbox)
        with _BYPASS_FILE_CHECK, _BYPASS_TEST_DISCOVERY:
            result = await validator.validate(changed_files=_FAKE_FILES)
        assert not result.passed
        assert result.lint_passed
        assert not result.typecheck_passed
        assert not result.tests_passed
        assert len(result.errors) == 1
        assert "typecheck" in result.errors[0]

    async def test_timeout_captured(self) -> None:
        sandbox = _make_sandbox(_sandbox_result(timed_out=True))
        validator = _make_validator(timeout_seconds=1, sandbox=sandbox)
        with _BYPASS_FILE_CHECK, _BYPASS_TEST_DISCOVERY:
            result = await validator.validate(changed_files=_FAKE_FILES)
        assert not result.passed
        assert not result.lint_passed
        assert "timed out" in result.errors[0]

    async def test_sandbox_execution_error_fails_closed(self) -> None:
        """A sandbox failure (e.g. Docker down) fails the step, not the host."""
        sandbox = _make_sandbox(execute_side_effect=RuntimeError("docker down"))
        validator = _make_validator(sandbox=sandbox)
        with _BYPASS_FILE_CHECK, _BYPASS_TEST_DISCOVERY:
            result = await validator.validate(changed_files=_FAKE_FILES)
        assert not result.passed
        assert not result.lint_passed
        assert "sandbox error" in result.errors[0]

    async def test_no_test_files_fails_closed(self) -> None:
        """When no test files are discovered, CI must fail."""
        validator = _make_validator(sandbox=_make_sandbox(_sandbox_result()))
        with (
            _BYPASS_FILE_CHECK,
            patch(
                "synthorg.meta.validation.ci_validator._discover_test_files",
                return_value=[],
            ),
        ):
            result = await validator.validate(changed_files=_FAKE_FILES)
        assert not result.passed
        assert not result.tests_passed
        assert any("no matching test files" in e for e in result.errors)


class TestDiscoverTestFiles:
    """Test file discovery tests."""

    def test_finds_test_file(self, tmp_path: Path) -> None:
        test_dir = tmp_path / "tests" / "unit" / "meta"
        test_dir.mkdir(parents=True)
        (test_dir / "test_new.py").write_text("# test")
        found = _discover_test_files(
            tmp_path,
            ("src/synthorg/meta/new.py",),
            _ALLOW_ALL,
        )
        assert len(found) == 1
        assert "test_new.py" in found[0]

    def test_finds_nested_test_file(self, tmp_path: Path) -> None:
        test_dir = tmp_path / "tests" / "unit" / "meta" / "strategies"
        test_dir.mkdir(parents=True)
        (test_dir / "test_algo.py").write_text("# test")
        found = _discover_test_files(
            tmp_path,
            ("src/synthorg/meta/strategies/algo.py",),
            _ALLOW_ALL,
        )
        assert len(found) == 1
        assert "strategies" in found[0]

    def test_missing_test_file_skipped(self, tmp_path: Path) -> None:
        test_dir = tmp_path / "tests" / "unit" / "meta"
        test_dir.mkdir(parents=True)
        found = _discover_test_files(
            tmp_path,
            ("src/synthorg/meta/no_tests.py",),
            _ALLOW_ALL,
        )
        assert found == []

    def test_non_python_files_skipped(self, tmp_path: Path) -> None:
        found = _discover_test_files(
            tmp_path,
            ("src/synthorg/meta/README.md",),
            _ALLOW_ALL,
        )
        assert found == []

    def test_out_of_scope_source_derives_no_test(self, tmp_path: Path) -> None:
        """A source outside the envelope yields no derived test target."""
        test_dir = tmp_path / "tests" / "unit" / "meta"
        test_dir.mkdir(parents=True)
        (test_dir / "test_new.py").write_text("# test")
        scope = ScopeValidator(
            allowed_paths=("src/synthorg/meta/strategies/*",),
            forbidden_paths=(),
        )
        found = _discover_test_files(
            tmp_path,
            ("src/synthorg/meta/new.py",),
            scope,
        )
        assert found == []

    def test_deduplicates(self, tmp_path: Path) -> None:
        test_dir = tmp_path / "tests" / "unit" / "meta"
        test_dir.mkdir(parents=True)
        (test_dir / "test_x.py").write_text("# test")
        found = _discover_test_files(
            tmp_path,
            ("src/synthorg/meta/x.py", "src/synthorg/meta/x.py"),
            _ALLOW_ALL,
        )
        assert len(found) == 1
