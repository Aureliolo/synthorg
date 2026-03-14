"""Tests for CodeRunnerTool with mocked sandbox."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.core.enums import ToolCategory
from synthorg.tools.code_runner import CodeRunnerTool
from synthorg.tools.sandbox.result import SandboxResult

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


# ── Helpers ──────────────────────────────────────────────────────


def _make_mock_sandbox(
    *,
    stdout: str = "output",
    stderr: str = "",
    returncode: int = 0,
    timed_out: bool = False,
) -> MagicMock:
    """Create a mock SandboxBackend with configurable result."""
    mock = MagicMock()
    mock.execute = AsyncMock(
        return_value=SandboxResult(
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            timed_out=timed_out,
        ),
    )
    return mock


# ── Init ─────────────────────────────────────────────────────────


class TestCodeRunnerInit:
    """Tool initialization."""

    def test_name(self) -> None:
        sandbox = _make_mock_sandbox()
        tool = CodeRunnerTool(sandbox=sandbox)
        assert tool.name == "code_runner"

    def test_category(self) -> None:
        sandbox = _make_mock_sandbox()
        tool = CodeRunnerTool(sandbox=sandbox)
        assert tool.category == ToolCategory.CODE_EXECUTION

    def test_has_parameters_schema(self) -> None:
        sandbox = _make_mock_sandbox()
        tool = CodeRunnerTool(sandbox=sandbox)
        schema = tool.parameters_schema
        assert schema is not None
        assert "code" in schema["properties"]
        assert "language" in schema["properties"]
        assert "timeout" in schema["properties"]
        assert schema["required"] == ["code", "language"]


# ── Language mapping ────────────────────────────────────────────


class TestCodeRunnerLanguageMapping:
    """Each language maps to the correct command."""

    @pytest.mark.parametrize(
        ("language", "expected_cmd", "expected_flag"),
        [
            ("python", "python3", "-c"),
            ("javascript", "node", "-e"),
            ("bash", "bash", "-c"),
        ],
    )
    async def test_language_command_mapping(
        self,
        language: str,
        expected_cmd: str,
        expected_flag: str,
    ) -> None:
        sandbox = _make_mock_sandbox()
        tool = CodeRunnerTool(sandbox=sandbox)

        await tool.execute(
            arguments={"code": "print('hi')", "language": language},
        )

        sandbox.execute.assert_awaited_once()
        call_kwargs = sandbox.execute.call_args.kwargs
        assert call_kwargs["command"] == expected_cmd
        assert call_kwargs["args"] == (expected_flag, "print('hi')")


# ── Success execution ───────────────────────────────────────────


class TestCodeRunnerSuccess:
    """Successful code execution."""

    async def test_success_returns_stdout(self) -> None:
        sandbox = _make_mock_sandbox(stdout="Hello, World!")
        tool = CodeRunnerTool(sandbox=sandbox)

        result = await tool.execute(
            arguments={"code": "print('Hello, World!')", "language": "python"},
        )

        assert not result.is_error
        assert result.content == "Hello, World!"
        assert result.metadata["returncode"] == 0
        assert result.metadata["language"] == "python"

    async def test_success_empty_stdout(self) -> None:
        sandbox = _make_mock_sandbox(stdout="")
        tool = CodeRunnerTool(sandbox=sandbox)

        result = await tool.execute(
            arguments={"code": "pass", "language": "python"},
        )

        assert not result.is_error
        assert result.content == "(no output)"


# ── Error execution ─────────────────────────────────────────────


class TestCodeRunnerErrors:
    """Error handling."""

    async def test_nonzero_returncode(self) -> None:
        sandbox = _make_mock_sandbox(
            stderr="SyntaxError: invalid syntax",
            returncode=1,
        )
        tool = CodeRunnerTool(sandbox=sandbox)

        result = await tool.execute(
            arguments={"code": "invalid(", "language": "python"},
        )

        assert result.is_error
        assert "SyntaxError" in result.content
        assert result.metadata["returncode"] == 1

    async def test_timeout_result(self) -> None:
        sandbox = _make_mock_sandbox(
            stderr="",
            returncode=-1,
            timed_out=True,
        )
        tool = CodeRunnerTool(sandbox=sandbox)

        result = await tool.execute(
            arguments={
                "code": "while True: pass",
                "language": "python",
                "timeout": 5.0,
            },
        )

        assert result.is_error
        assert "timed out" in result.content.lower()
        assert result.metadata["timed_out"] is True

    async def test_unsupported_language(self) -> None:
        sandbox = _make_mock_sandbox()
        tool = CodeRunnerTool(sandbox=sandbox)

        result = await tool.execute(
            arguments={"code": "puts 'hi'", "language": "ruby"},
        )

        assert result.is_error
        assert "Unsupported language" in result.content
        assert "ruby" in result.content
        sandbox.execute.assert_not_awaited()


# ── Timeout forwarding ──────────────────────────────────────────


class TestCodeRunnerTimeout:
    """Timeout parameter forwarding to sandbox."""

    async def test_timeout_forwarded(self) -> None:
        sandbox = _make_mock_sandbox()
        tool = CodeRunnerTool(sandbox=sandbox)

        await tool.execute(
            arguments={
                "code": "time.sleep(1)",
                "language": "python",
                "timeout": 42.0,
            },
        )

        call_kwargs = sandbox.execute.call_args.kwargs
        assert call_kwargs["timeout"] == 42.0

    async def test_no_timeout_passed_as_none(self) -> None:
        sandbox = _make_mock_sandbox()
        tool = CodeRunnerTool(sandbox=sandbox)

        await tool.execute(
            arguments={"code": "print(1)", "language": "python"},
        )

        call_kwargs = sandbox.execute.call_args.kwargs
        assert call_kwargs["timeout"] is None


# ── Missing code parameter ──────────────────────────────────────


class TestCodeRunnerMissingParams:
    """Behavior with missing required parameters."""

    async def test_missing_code_raises(self) -> None:
        sandbox = _make_mock_sandbox()
        tool = CodeRunnerTool(sandbox=sandbox)

        with pytest.raises(KeyError, match="code"):
            await tool.execute(
                arguments={"language": "python"},
            )

    async def test_missing_language_raises(self) -> None:
        sandbox = _make_mock_sandbox()
        tool = CodeRunnerTool(sandbox=sandbox)

        with pytest.raises(KeyError, match="language"):
            await tool.execute(
                arguments={"code": "print(1)"},
            )
