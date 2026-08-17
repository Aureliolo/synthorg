"""Unit tests for test-run recognition and receipt capture.

Which executions produce a ``CodeExecutionRecord`` decides whether the
build/test oracle can verify a deliverable at all, so the classifier's
boundaries are the tests that matter: a real suite must be recognised
through whatever wrapper an agent used, and a trivial script must not be
able to pass itself off as one.
"""

import pytest

from synthorg.core.clock import Clock
from synthorg.core.execution_identity import (
    ExecutionIdentity,
    execution_identity_scope,
)
from synthorg.core.types import NotBlankStr
from synthorg.persistence.code_execution_protocol import (
    CodeExecutionPurpose,
    CodeExecutionRecord,
    CodeExecutionRecordRepository,
)
from synthorg.tools._test_run_capture import is_test_run, record_if_test_run
from synthorg.tools.sandbox.result import SandboxResult
from tests._shared import FakeClock, mock_of
from tests.unit.deliverable_receipts._fakes import RecordingCodeExecutionStore

pytestmark = pytest.mark.unit

_COMMAND_LIMIT = 500
_TAIL_LIMIT = 2000


def _result(*, returncode: int = 0, timed_out: bool = False) -> SandboxResult:
    """Build a finished sandbox execution.

    Returns:
        The synthetic result the capture path records.
    """
    return SandboxResult(
        stdout="3 passed",
        stderr="",
        returncode=returncode,
        timed_out=timed_out,
    )


async def _capture(
    store: RecordingCodeExecutionStore,
    command: str,
    *,
    clock: Clock | None = None,
    result: SandboxResult | None = None,
) -> None:
    """Run the capture path for *command* inside a bound execution scope."""
    identity = ExecutionIdentity(
        task_id=NotBlankStr("task-1"),
        execution_id=NotBlankStr("exec-1"),
        project_id=NotBlankStr("proj-1"),
    )
    with execution_identity_scope(identity):
        await record_if_test_run(
            result or _result(),
            command=command,
            records=store.repository,
            clock=clock or FakeClock(),
            command_repr_limit=_COMMAND_LIMIT,
            output_tail_limit=_TAIL_LIMIT,
        )


class TestIsTestRun:
    @pytest.mark.parametrize(
        "command",
        [
            "pytest",
            "pytest -q tests/",
            "python -m pytest tests/unit",
            "uv run pytest --maxfail=1",
            "bash -c pytest",
            "python -m unittest discover",
            "npm test",
            "npm run test",
            "pnpm run test -- --watch=false",
            "yarn test",
            "bun test",
            "deno test",
            "npx vitest run",
            "npx jest --ci",
            "go test ./...",
            "cargo test --all-features",
            "dotnet test",
            "mvn -B test",
            "gradle --no-daemon test",
            "make test",
            "tox -e py314",
            "nox -s tests",
            "bundle exec rspec",
            "vendor/bin/phpunit",
            "ctest --output-on-failure",
            "PYTEST_ADDOPTS=-q pytest",
            "node --test",
            "node --test test/",
            "node --test --test-reporter=spec",
        ],
    )
    def test_recognised_runners(self, command: str) -> None:
        assert is_test_run(command)

    @pytest.mark.parametrize(
        "command",
        [
            "echo ok",
            "ls -la",
            "python -c 'print(1)'",
            "git status",
            "npm install",
            "npm run build",
            "go build ./...",
            "cargo build",
            "cat pytest_helper.py",
            "python testing_utils.py",
            "node server.js",
            "node --watch server.js",
            "node --test-reporter=spec run.js",
        ],
    )
    def test_unrecognised_commands(self, command: str) -> None:
        """A false positive lets a trivial script stand in for a suite.

        That is worse than a false negative: an unrecognised runner reads
        as UNVERIFIED, which blocks rather than passes.
        """
        assert not is_test_run(command)

    def test_recognition_is_case_insensitive(self) -> None:
        assert is_test_run("PYTEST -q")


class TestForgedTestEvidence:
    """The classifier decides whether a build can be verified at all.

    The command is model-supplied, so an agent whose suite failed must not
    be able to mint a passing receipt. Every case here exits 0 while running
    no suite; each would flip the build/test oracle from blocked to verified
    and reach the initiative judge labelled "written by the sandbox, not
    reported by an agent".
    """

    @pytest.mark.parametrize(
        "command",
        [
            "echo pytest",
            "echo 'go test'",
            "cat pytest.ini",
            "grep -r pytest .",
            "pip install pytest",
            "npm install jest",
            "gem install rspec",
            "true # pytest",
            "ls jest",
            # `test` is a real npm package, so these install it and exit 0.
            # A package-manager verb other than `test` never ran a suite.
            "npm install test",
            "yarn add test",
            "npm uninstall test",
            "pnpm add test",
            "bun add test",
        ],
    )
    def test_naming_a_runner_is_not_running_one(self, command: str) -> None:
        assert not is_test_run(command)

    @pytest.mark.parametrize(
        "command",
        [
            "npm test",
            "npm run test",
            "pnpm run test -- --watch=false",
            "yarn test",
            "deno test",
            "mvn clean test",
            "make -j4 test",
        ],
    )
    def test_a_runner_reaching_its_test_target_is_a_test_run(
        self, command: str
    ) -> None:
        """A package manager's ``test`` verb, and a build tool's target."""
        assert is_test_run(command)

    @pytest.mark.parametrize(
        "command",
        [
            "pytest || true",
            "pytest ; echo done",
            "pytest -q &",
            "(pytest -q)",
            "echo `pytest`",
            "echo $(pytest)",
        ],
    )
    def test_a_status_masking_command_is_refused(self, command: str) -> None:
        """The recorded ``passed`` would describe the tail, not the suite.

        Each of these can exit 0 whatever pytest did, or runs a program the
        parse never sees, so accepting one records a green suite for a red
        one.
        """
        assert not is_test_run(command)

    @pytest.mark.parametrize(
        "command",
        [
            "pytest -q && echo ok",
            "pytest -q | tee out.txt",
            "pytest -q > out.txt",
            "cd /workspace && npm test 2>&1 | tail -12",
            'cd /workspace && npm test 2>&1 | tee /tmp/run.txt | grep -E "pass|fail"',
            "cd /workspace && go test ./... 2>/dev/null",
            "cd /workspace && npm test",
        ],
    )
    def test_a_conjunctive_command_is_a_test_run(self, command: str) -> None:
        """The shape agents actually type, and its status is trustworthy.

        A line built only of ``&&`` and ``|`` exits zero only when every
        command in it exited zero: ``&&`` short-circuits, and ``|`` is
        conjunctive under the ``pipefail`` every agent line runs with.
        Refusing these produced 181 shell commands, several green suites and
        zero evidence on a live run, and the oracle blocked all of them.
        """
        assert is_test_run(command)

    def test_a_quoted_pipe_does_not_split_the_line(self) -> None:
        """Only an operator separates commands, never a character in a word."""
        assert not is_test_run('echo "pytest | grep"')


class TestRecordIfTestRun:
    async def test_a_test_run_is_recorded(self) -> None:
        store = RecordingCodeExecutionStore()

        await _capture(store, "pytest -q tests/")

        assert len(store.records) == 1
        record = store.records[0]
        assert record.purpose is CodeExecutionPurpose.TESTS
        assert record.command == "pytest -q tests/"
        assert record.passed is True

    async def test_a_non_test_run_writes_nothing(self) -> None:
        """A general command produces no evidence, so none is invented."""
        store = RecordingCodeExecutionStore()

        await _capture(store, "echo hello")

        assert store.records == []

    async def test_a_failing_suite_is_recorded_as_failed(self) -> None:
        """Evidence of failure is evidence: the oracle needs the verdict."""
        store = RecordingCodeExecutionStore()

        await _capture(store, "pytest", result=_result(returncode=1))

        assert store.records[0].passed is False
        assert store.records[0].returncode == 1

    async def test_a_timed_out_suite_is_recorded(self) -> None:
        store = RecordingCodeExecutionStore()

        await _capture(store, "pytest", result=_result(returncode=124, timed_out=True))

        assert store.records[0].timed_out is True
        assert store.records[0].passed is False

    async def test_outside_an_execution_scope_writes_nothing(self) -> None:
        """No bound run means no task to attribute the receipt to."""
        store = RecordingCodeExecutionStore()

        await record_if_test_run(
            _result(),
            command="pytest",
            records=store.repository,
            clock=FakeClock(),
            command_repr_limit=_COMMAND_LIMIT,
            output_tail_limit=_TAIL_LIMIT,
        )

        assert store.records == []

    async def test_no_repository_is_a_noop(self) -> None:
        await record_if_test_run(
            _result(),
            command="pytest",
            records=None,
            clock=FakeClock(),
            command_repr_limit=_COMMAND_LIMIT,
            output_tail_limit=_TAIL_LIMIT,
        )

    async def test_a_store_failure_does_not_fail_the_run(self) -> None:
        """Losing the receipt must never lose the run it describes."""

        async def _boom(_record: CodeExecutionRecord, /) -> None:
            msg = "receipt store unavailable"
            raise OSError(msg)

        store = mock_of[CodeExecutionRecordRepository](append=_boom)
        identity = ExecutionIdentity(
            task_id=NotBlankStr("task-1"),
            execution_id=NotBlankStr("exec-1"),
            project_id=NotBlankStr("proj-1"),
        )
        with execution_identity_scope(identity):
            await record_if_test_run(
                _result(),
                command="pytest",
                records=store,
                clock=FakeClock(),
                command_repr_limit=_COMMAND_LIMIT,
                output_tail_limit=_TAIL_LIMIT,
            )

    async def test_command_and_output_are_length_bounded(self) -> None:
        store = RecordingCodeExecutionStore()
        long_command = "pytest " + ("x" * 1000)

        with execution_identity_scope(
            ExecutionIdentity(
                task_id=NotBlankStr("task-1"),
                execution_id=NotBlankStr("exec-1"),
                project_id=NotBlankStr("proj-1"),
            )
        ):
            await record_if_test_run(
                SandboxResult(
                    stdout="y" * 5000, stderr="z" * 5000, returncode=0, timed_out=False
                ),
                command=long_command,
                records=store.repository,
                clock=FakeClock(),
                command_repr_limit=_COMMAND_LIMIT,
                output_tail_limit=_TAIL_LIMIT,
            )

        record = store.records[0]
        assert len(record.command) == _COMMAND_LIMIT
        assert record.stdout_tail is not None
        assert len(record.stdout_tail) == _TAIL_LIMIT
        assert record.stderr_tail is not None
        assert len(record.stderr_tail) == _TAIL_LIMIT
