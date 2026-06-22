"""Unit tests for local CI validator."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synthorg.meta.validation.ci_validator import (
    LocalCIValidator,
    _discover_test_files,
    _existing_py_files,
    _is_safe_ci_path,
)
from synthorg.meta.validation.scope_validator import ScopeValidator

pytestmark = pytest.mark.unit

# Permissive envelope for tests that exercise the safety / discovery logic
# independently of scope gating; scope-specific behaviour has dedicated tests.
_ALLOW_ALL = ScopeValidator(allowed_paths=("*",), forbidden_paths=())


def _make_validator(
    *,
    timeout_seconds: int = 10,
    scope_validator: ScopeValidator = _ALLOW_ALL,
) -> LocalCIValidator:
    """Build a validator rooted at the (absolute) current directory."""
    return LocalCIValidator(
        project_root=Path.cwd(),
        scope_validator=scope_validator,
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


def _mock_subprocess(
    returncode: int = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
) -> AsyncMock:
    """Create a mock subprocess that returns the given code."""
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


class TestLocalCIValidator:
    """LocalCIValidator tests."""

    def test_init_rejects_relative_project_root(self) -> None:
        with pytest.raises(ValueError, match="absolute"):
            LocalCIValidator(
                project_root=Path("relative/root"),
                scope_validator=_ALLOW_ALL,
            )

    async def test_all_steps_pass(self) -> None:
        validator = _make_validator()
        mock_proc = _mock_subprocess(returncode=0)
        with (
            patch(
                "synthorg.meta.validation.ci_validator.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
            _BYPASS_FILE_CHECK,
            _BYPASS_TEST_DISCOVERY,
        ):
            result = await validator.validate(
                changed_files=_FAKE_FILES,
            )
        assert result.passed
        assert result.lint_passed
        assert result.typecheck_passed
        assert result.tests_passed
        assert result.errors == ()
        assert result.duration_seconds >= 0.0

    async def test_lint_failure_short_circuits(self) -> None:
        validator = _make_validator()
        fail_proc = _mock_subprocess(
            returncode=1,
            stdout=b"E501 line too long",
        )
        call_count = 0

        async def counting_create(*args: object, **kwargs: object) -> AsyncMock:
            nonlocal call_count
            call_count += 1
            return fail_proc

        with (
            patch(
                "synthorg.meta.validation.ci_validator.asyncio.create_subprocess_exec",
                side_effect=counting_create,
            ),
            _BYPASS_FILE_CHECK,
            _BYPASS_TEST_DISCOVERY,
        ):
            result = await validator.validate(
                changed_files=_FAKE_FILES,
            )
        assert not result.passed
        assert not result.lint_passed
        assert not result.typecheck_passed
        assert not result.tests_passed
        assert len(result.errors) == 1
        assert "lint" in result.errors[0]
        # Only lint was called (short-circuit).
        assert call_count == 1

    async def test_typecheck_failure_skips_tests(self) -> None:
        validator = _make_validator()
        pass_proc = _mock_subprocess(returncode=0)
        fail_proc = _mock_subprocess(
            returncode=1,
            stderr=b"error: incompatible types",
        )
        calls = [pass_proc, fail_proc]

        async def sequential_create(*args: object, **kwargs: object) -> AsyncMock:
            return calls.pop(0)

        with (
            patch(
                "synthorg.meta.validation.ci_validator.asyncio.create_subprocess_exec",
                side_effect=sequential_create,
            ),
            _BYPASS_FILE_CHECK,
            _BYPASS_TEST_DISCOVERY,
        ):
            result = await validator.validate(
                changed_files=_FAKE_FILES,
            )
        assert not result.passed
        assert result.lint_passed
        assert not result.typecheck_passed
        assert not result.tests_passed
        assert len(result.errors) == 1
        assert "typecheck" in result.errors[0]

    async def test_timeout_captured(self) -> None:
        validator = _make_validator(timeout_seconds=1)

        async def timeout_create(*args: object, **kwargs: object) -> AsyncMock:
            proc = AsyncMock()

            async def slow_communicate() -> None:
                raise TimeoutError

            proc.communicate = slow_communicate
            # ``Process.kill`` is synchronous on a real subprocess; keep it a
            # plain mock so the timeout path's un-awaited ``proc.kill()`` does
            # not leave a dangling coroutine for the GC to warn about.
            proc.kill = MagicMock()
            return proc

        with (
            patch(
                "synthorg.meta.validation.ci_validator.asyncio.create_subprocess_exec",
                side_effect=timeout_create,
            ),
            _BYPASS_FILE_CHECK,
            _BYPASS_TEST_DISCOVERY,
        ):
            result = await validator.validate(
                changed_files=_FAKE_FILES,
            )
        assert not result.passed
        assert not result.lint_passed
        assert "timed out" in result.errors[0]

    async def test_command_not_found(self) -> None:
        validator = _make_validator()

        async def fnf_create(*args: object, **kwargs: object) -> None:
            raise FileNotFoundError

        with (
            patch(
                "synthorg.meta.validation.ci_validator.asyncio.create_subprocess_exec",
                side_effect=fnf_create,
            ),
            _BYPASS_FILE_CHECK,
            _BYPASS_TEST_DISCOVERY,
        ):
            result = await validator.validate(
                changed_files=_FAKE_FILES,
            )
        assert not result.passed
        assert "command not found" in result.errors[0]

    async def test_no_test_files_fails_closed(self) -> None:
        """When no test files are discovered, CI must fail."""
        validator = _make_validator()
        mock_proc = _mock_subprocess(returncode=0)
        with (
            patch(
                "synthorg.meta.validation.ci_validator.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
            _BYPASS_FILE_CHECK,
            patch(
                "synthorg.meta.validation.ci_validator._discover_test_files",
                return_value=[],
            ),
        ):
            result = await validator.validate(
                changed_files=_FAKE_FILES,
            )
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
