"""Unit tests for local CI validator."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from synthorg.meta.validation.ci_validator import (
    LocalCIValidator,
    _discover_test_files,
    _existing_py_files,
    _is_safe_ci_path,
)

pytestmark = pytest.mark.unit


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
        result = _existing_py_files(tmp_path, ("real.py", "--plugin=evil.py"))
        # The validated file is forwarded as a resolved absolute path.
        assert result == [str((tmp_path / "real.py").resolve())]

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

    async def test_all_steps_pass(self) -> None:
        validator = LocalCIValidator(timeout_seconds=10)
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
                project_root=Path("/fake/root"),
                changed_files=_FAKE_FILES,
            )
        assert result.passed
        assert result.lint_passed
        assert result.typecheck_passed
        assert result.tests_passed
        assert result.errors == ()
        assert result.duration_seconds >= 0.0

    async def test_lint_failure_short_circuits(self) -> None:
        validator = LocalCIValidator(timeout_seconds=10)
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
                project_root=Path("/fake/root"),
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
        validator = LocalCIValidator(timeout_seconds=10)
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
                project_root=Path("/fake/root"),
                changed_files=_FAKE_FILES,
            )
        assert not result.passed
        assert result.lint_passed
        assert not result.typecheck_passed
        assert not result.tests_passed
        assert len(result.errors) == 1
        assert "typecheck" in result.errors[0]

    async def test_timeout_captured(self) -> None:
        validator = LocalCIValidator(timeout_seconds=1)

        async def timeout_create(*args: object, **kwargs: object) -> AsyncMock:
            proc = AsyncMock()

            async def slow_communicate() -> None:
                raise TimeoutError

            proc.communicate = slow_communicate
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
                project_root=Path("/fake/root"),
                changed_files=_FAKE_FILES,
            )
        assert not result.passed
        assert not result.lint_passed
        assert "timed out" in result.errors[0]

    async def test_command_not_found(self) -> None:
        validator = LocalCIValidator(timeout_seconds=10)

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
                project_root=Path("/fake/root"),
                changed_files=_FAKE_FILES,
            )
        assert not result.passed
        assert "command not found" in result.errors[0]

    async def test_no_test_files_fails_closed(self) -> None:
        """When no test files are discovered, CI must fail."""
        validator = LocalCIValidator(timeout_seconds=10)
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
                project_root=Path("/fake/root"),
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
        )
        assert len(found) == 1
        assert "strategies" in found[0]

    def test_missing_test_file_skipped(self, tmp_path: Path) -> None:
        test_dir = tmp_path / "tests" / "unit" / "meta"
        test_dir.mkdir(parents=True)
        found = _discover_test_files(
            tmp_path,
            ("src/synthorg/meta/no_tests.py",),
        )
        assert found == []

    def test_non_python_files_skipped(self, tmp_path: Path) -> None:
        found = _discover_test_files(
            tmp_path,
            ("src/synthorg/meta/README.md",),
        )
        assert found == []

    def test_deduplicates(self, tmp_path: Path) -> None:
        test_dir = tmp_path / "tests" / "unit" / "meta"
        test_dir.mkdir(parents=True)
        (test_dir / "test_x.py").write_text("# test")
        found = _discover_test_files(
            tmp_path,
            ("src/synthorg/meta/x.py", "src/synthorg/meta/x.py"),
        )
        assert len(found) == 1
