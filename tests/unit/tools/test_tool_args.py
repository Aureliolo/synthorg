"""Tests for typed args of single-tool / small-cluster domains.

Each args model lives in its consuming tool module; this suite exercises
them from those homes.
"""

import pytest
from pydantic import ValidationError

from synthorg.tools.approval_tool import RequestHumanApprovalArgs
from synthorg.tools.code_runner import CodeRunnerArgs
from synthorg.tools.context.compact_context import CompactContextArgs
from synthorg.tools.discovery import (
    ListToolsArgs,
    LoadToolArgs,
    LoadToolResourceArgs,
)
from synthorg.tools.examples.echo import EchoArgs
from synthorg.tools.terminal.shell_command import ShellCommandArgs


class TestShellCommandArgs:
    @pytest.mark.unit
    def test_minimal(self) -> None:
        args = ShellCommandArgs(command="ls -la")
        assert args.working_directory is None
        assert args.timeout is None

    @pytest.mark.unit
    def test_timeout_bounds(self) -> None:
        ShellCommandArgs(command="x", timeout=1)
        ShellCommandArgs(command="x", timeout=600)
        with pytest.raises(ValidationError):
            ShellCommandArgs(command="x", timeout=0.5)
        with pytest.raises(ValidationError):
            ShellCommandArgs(command="x", timeout=601)


class TestCodeRunnerArgs:
    @pytest.mark.unit
    def test_supported_languages(self) -> None:
        for lang in ("python", "javascript", "bash"):
            args = CodeRunnerArgs.model_validate(
                {"code": "x", "language": lang},
            )
            assert args.language == lang

    @pytest.mark.unit
    def test_unsupported_language_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CodeRunnerArgs.model_validate(
                {"code": "x", "language": "ruby"},
            )

    @pytest.mark.unit
    def test_empty_code_allowed(self) -> None:
        args = CodeRunnerArgs(code="", language="python")
        assert args.code == ""


class TestRequestHumanApprovalArgs:
    @pytest.mark.unit
    def test_construction(self) -> None:
        args = RequestHumanApprovalArgs(
            action_type="deploy:production",
            title="Promote build 42",
            description="Push v0.7.2 to production after staging soak",
        )
        assert args.action_type == "deploy:production"

    @pytest.mark.unit
    def test_action_type_max_length(self) -> None:
        with pytest.raises(ValidationError):
            RequestHumanApprovalArgs(
                action_type="x" * 129,
                title="x",
                description="x",
            )

    @pytest.mark.unit
    def test_title_max_length(self) -> None:
        with pytest.raises(ValidationError):
            RequestHumanApprovalArgs(
                action_type="x:y",
                title="x" * 257,
                description="x",
            )

    @pytest.mark.unit
    def test_description_max_length(self) -> None:
        with pytest.raises(ValidationError):
            RequestHumanApprovalArgs(
                action_type="x:y",
                title="x",
                description="x" * 4097,
            )


class TestDiscoveryArgs:
    @pytest.mark.unit
    def test_list_tools_no_filter(self) -> None:
        args = ListToolsArgs()
        assert args.category is None

    @pytest.mark.unit
    def test_load_tool_requires_name(self) -> None:
        with pytest.raises(ValidationError):
            LoadToolArgs.model_validate({})

    @pytest.mark.unit
    def test_load_tool_resource(self) -> None:
        args = LoadToolResourceArgs(tool_name="x", resource_id="r1")
        assert args.tool_name == "x"


class TestMiscArgs:
    @pytest.mark.unit
    def test_echo(self) -> None:
        args = EchoArgs(message="hi")
        assert args.message == "hi"

    @pytest.mark.unit
    def test_compact_context_construction(self) -> None:
        args = CompactContextArgs(
            strategy="summarize",
            reason="context fill is high and accuracy is critical",
        )
        assert args.strategy == "summarize"
        assert args.preserve_markers is True

    @pytest.mark.unit
    def test_compact_context_blank_reason_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CompactContextArgs(strategy="summarize", reason="   ")
