"""Unit tests for test-run recognition and receipt capture.

Which executions produce a ``CodeExecutionRecord`` decides whether the
build/test oracle can verify a deliverable at all, so the classifier's
boundaries are the tests that matter: a real suite must be recognised
through whatever wrapper an agent used, and a trivial script must not be
able to pass itself off as one.
"""

from pathlib import Path

import pytest

from synthorg.core.clock import Clock
from synthorg.core.execution_identity import (
    ExecutionIdentity,
    execution_identity_scope,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.workspace.environment.config import DEFAULT_MANIFEST_FILENAME
from synthorg.persistence.code_execution_protocol import (
    CodeExecutionPurpose,
    CodeExecutionRecord,
    CodeExecutionRecordRepository,
)
from synthorg.tools._test_run_capture import (
    is_test_run,
    record_if_test_run,
    redacted_command,
    redacted_tail,
)
from synthorg.tools.sandbox.result import SandboxResult
from tests._shared import FakeClock, mock_of
from tests.unit.deliverable_receipts._fakes import RecordingCodeExecutionStore

pytestmark = pytest.mark.unit

_COMMAND_LIMIT = 500
_TAIL_LIMIT = 2000
_PROJECT = "proj-1"


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
    workspace_root: Path | None = None,
) -> None:
    """Run the capture path for *command* inside a bound execution scope."""
    identity = ExecutionIdentity(
        task_id=NotBlankStr("task-1"),
        execution_id=NotBlankStr("exec-1"),
        project_id=NotBlankStr(_PROJECT),
    )
    with execution_identity_scope(identity):
        await record_if_test_run(
            result or _result(),
            command=command,
            records=store.repository,
            clock=clock or FakeClock(),
            command_repr_limit=_COMMAND_LIMIT,
            output_tail_limit=_TAIL_LIMIT,
            workspace_root=workspace_root,
        )


def _project_declaring_a_lint_gate(tmp_path: Path) -> Path:
    """Seed a project whose committed manifest declares a lint command.

    Returns:
        The workspace root the capture path is handed.
    """
    workspace = tmp_path / "projects" / _PROJECT
    workspace.mkdir(parents=True)
    (workspace / DEFAULT_MANIFEST_FILENAME).write_text(
        'language: python\ntest_command: pytest\nlint_command: "ruff check ."\n',
        encoding="utf-8",
    )
    return tmp_path


class TestADeclaredGateProducesItsOwnEvidence:
    """The write side of the declared-gate mechanism, end to end.

    The oracle blocks a unit whose project declares a gate with no passing run,
    so if the workspace stops reaching this path every such project is refused
    for ever with nothing to act on. That is the failure mode the sandbox
    category-forwarding incident had verbatim: a parameter threaded through and
    omitted at every call site, with no test naming it.
    """

    async def test_a_run_of_the_declared_command_is_recorded_as_that_gate(
        self, tmp_path: Path
    ) -> None:
        store = RecordingCodeExecutionStore()

        await _capture(
            store,
            "ruff check .",
            workspace_root=_project_declaring_a_lint_gate(tmp_path),
        )

        assert [record.purpose for record in store.records] == [
            CodeExecutionPurpose.LINT
        ]

    async def test_the_command_is_found_inside_the_line_an_agent_types(
        self, tmp_path: Path
    ) -> None:
        """``cd x && ruff check . | tail`` is the shape agents actually run.

        Comparing the declaration against the whole line recognises none of
        them, which withholds the evidence the agent genuinely produced and
        then refuses the unit for not producing it.
        """
        store = RecordingCodeExecutionStore()

        await _capture(
            store,
            "cd src && ruff check . 2>&1 | tail -20",
            workspace_root=_project_declaring_a_lint_gate(tmp_path),
        )

        assert [record.purpose for record in store.records] == [
            CodeExecutionPurpose.LINT
        ]

    async def test_a_line_whose_status_says_nothing_records_nothing(
        self, tmp_path: Path
    ) -> None:
        """``|| true`` exits zero whatever the linter did."""
        store = RecordingCodeExecutionStore()

        await _capture(
            store,
            "ruff check . || true",
            workspace_root=_project_declaring_a_lint_gate(tmp_path),
        )

        assert store.records == []

    async def test_without_a_workspace_no_gate_is_recognised(
        self, tmp_path: Path
    ) -> None:
        """The regression this class exists for: the parameter stops arriving.

        Nothing raises and nothing looks wrong; the project's declared gate
        simply never accrues evidence, and every unit under it is blocked.
        """
        _project_declaring_a_lint_gate(tmp_path)
        store = RecordingCodeExecutionStore()

        await _capture(store, "ruff check .", workspace_root=None)

        assert store.records == []


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

    @pytest.mark.parametrize(
        "command",
        [
            "pytest -q\necho ok",
            "npm test\ngit commit -m x",
            "pytest -q\recho ok",
            "pytest -q\r\necho ok",
        ],
    )
    def test_a_second_statement_is_refused(self, command: str) -> None:
        """The line's status is the LAST statement's, so this proves nothing.

        Checked against the raw line rather than the tokens: ``shlex`` lists
        newline in ``whitespace``, so it consumes the separator and hands
        back ``['pytest', '-q', 'echo', 'ok']``. A per-token guard therefore
        never fires, and the segment reads as one command headed ``pytest``
        whose recorded status is ``echo``'s zero.
        """
        assert not is_test_run(command)

    @pytest.mark.parametrize(
        "command",
        [
            'bash -c "npm test 2>&1 | tail -5"',
            'sh -c "pytest -q | tee out.txt"',
        ],
    )
    def test_a_pipeline_inside_a_nested_shell_is_refused(self, command: str) -> None:
        """``pipefail`` is a shell option, and a fresh shell does not inherit it.

        The conjunctive reading of ``|`` rests on the wrapper setting
        ``-o pipefail`` on the shell it starts. The payload here runs in a
        shell that invocation started, without the option, so the pipeline
        reports ``tail``'s zero while the suite failed.
        """
        assert not is_test_run(command)

    def test_a_nested_shell_without_a_pipeline_is_still_a_test_run(self) -> None:
        """The nested-shell rule targets the pipeline, not the nesting."""
        assert is_test_run('bash -c "pytest -q"')

    @pytest.mark.parametrize(
        "command",
        [
            "set +o pipefail && pytest -q | tail -5",
            "set +o pipefail && npm test 2>&1 | tail",
        ],
    )
    def test_a_line_that_turns_pipefail_off_before_a_pipe_is_refused(
        self, command: str
    ) -> None:
        """The line can revoke the option the pipe's trust rests on.

        ``|`` is read as conjunctive only because the wrapper runs the line
        under ``pipefail``. A line whose first command unsets it puts the
        pipeline back to reporting ``tail``'s status, so the suite can fail
        and the line still exit zero, which is the forged pass this module
        exists to refuse.
        """
        assert not is_test_run(command)

    def test_turning_pipefail_on_is_not_a_refusal(self) -> None:
        """The rule targets the disable, not the builtin."""
        assert is_test_run("set -o pipefail && pytest -q | tail -5")

    def test_turning_pipefail_off_then_on_again_protects_the_pipe(self) -> None:
        """The option is a toggle, not a one-way latch.

        Reading only the disable makes the first ``set +o pipefail`` on a line
        permanent, so a line that restores the option before its pipeline is
        refused. That withholds the evidence a genuine test run produced,
        which is the same cost as refusing ``&&`` and ``|`` outright.
        """
        assert is_test_run("set +o pipefail && set -o pipefail && pytest -q | tail -5")

    def test_the_last_toggle_before_the_pipe_is_the_one_that_counts(self) -> None:
        """Re-disabling after re-enabling is still a disable.

        Ordering is what this asks about, so the enable sits between two
        disables: a reading that let any enable anywhere on the line win
        would accept it, and the state at the pipe is the only thing that
        decides how the pipeline reports.
        """
        assert not is_test_run(
            "set +o pipefail && set -o pipefail && set +o pipefail "
            "&& pytest -q | tail -5"
        )

    def test_a_bundled_disable_is_still_a_disable(self) -> None:
        """``set +eo pipefail`` is the same instruction as ``set +o pipefail``.

        A shell bundles short flags, so the option letter arrives inside one
        token. Matching the token whole answers "says nothing about pipefail"
        and leaves it believed ON, and the pipeline below then reports its
        LAST command's status while being read as its first command's. That
        is a pass recorded for a suite whose result was never consulted,
        which is the forgery this module exists to refuse.
        """
        assert not is_test_run("set +eo pipefail && pytest -q | tail -5")

    def test_a_bundled_enable_is_still_an_enable(self) -> None:
        """``set -euo pipefail`` is how this line is ordinarily written.

        The other direction of the same reading: refusing it would withhold
        the evidence a genuinely protected pipeline produced.
        """
        assert is_test_run("set -euo pipefail && pytest -q | tail -5")

    def test_a_bundled_enable_restores_a_bundled_disable(self) -> None:
        """Both spellings have to toggle, or the pair cannot round-trip."""
        assert is_test_run("set +eo pipefail && set -euo pipefail && pytest -q | tail")

    def test_the_last_option_in_one_set_command_is_the_one_that_lands(self) -> None:
        """``set -o pipefail +o pipefail`` leaves the option OFF.

        The shell applies the options of a single ``set`` left to right, so
        the state it leaves behind is the last one named. Reading the first
        inverts the answer on exactly this line, which then reads a pipeline
        that masks its exit status as evidence the suite passed.
        """
        assert not is_test_run("set -o pipefail +o pipefail && pytest -q | tail -5")

    def test_the_last_option_wins_in_the_other_order_too(self) -> None:
        """The mirror image, so the rule cannot be "a disable anywhere"."""
        assert is_test_run("set +o pipefail -o pipefail && pytest -q | tail -5")

    def test_a_toggle_inside_a_pipeline_does_not_reach_the_line(self) -> None:
        """Every component of a pipeline runs in a subshell.

        So ``set +o pipefail | cat`` turns the option off in that subshell
        and exits, leaving the line's own option untouched. Persisting it
        would refuse a later pipeline that really is protected, discarding
        the evidence a genuine test run produced.
        """
        assert is_test_run("set +o pipefail | cat && pytest -q | tail -5")

    def test_a_combined_set_reads_the_flag_next_to_the_option(self) -> None:
        """``set +o errexit -o pipefail`` enables pipefail, whatever else it does.

        One command can carry both flags, so the answer is the flag directly
        before ``pipefail``, not whichever flag appears somewhere in the line.
        """
        assert is_test_run("set +o errexit -o pipefail && pytest -q | tail -5")

    def test_unsetting_another_option_leaves_the_pipe_alone(self) -> None:
        """Only ``pipefail`` decides how a pipeline reports its status."""
        assert is_test_run("set +o errexit && pytest -q | tail -5")


class TestRecordIfTestRun:
    async def test_a_credential_in_the_output_is_masked_before_the_row_exists(
        self,
    ) -> None:
        """The tail is rendered into the reviewer's prompt, fenced but readable."""
        store = RecordingCodeExecutionStore()
        leaked = SandboxResult(
            stdout="Authorization: Bearer sk-live-abcdef1234567890\n3 passed",
            stderr="AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE",
            returncode=0,
            timed_out=False,
        )

        await _capture(store, "pytest -q tests/", result=leaked)

        record = store.records[0]
        assert record.stdout_tail is not None
        assert "sk-live-abcdef" not in record.stdout_tail
        assert "3 passed" in record.stdout_tail
        assert record.stderr_tail is not None
        assert "AKIAIOSFODNN7EXAMPLE" not in record.stderr_tail

    async def test_a_credential_in_the_command_is_masked_before_the_row_exists(
        self,
    ) -> None:
        """``KEY=secret pytest`` is how an agent hands a key to a run.

        The command is rendered beside the tails as verification evidence,
        so a secret typed into it reaches the reviewer's prompt the same way
        a printed one would.
        """
        store = RecordingCodeExecutionStore()

        await _capture(store, "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE pytest -q")

        record = store.records[0]
        assert record.purpose is CodeExecutionPurpose.TESTS
        assert "AKIAIOSFODNN7EXAMPLE" not in record.command
        assert "pytest -q" in record.command

    def test_a_credential_straddling_the_command_cut_is_still_masked(self) -> None:
        command = "Authorization: Bearer secretvalue1234567890 " + "x" * 40

        head = redacted_command(command, limit=30)

        assert "secretvalue" not in head
        assert len(head) <= 30

    def test_a_credential_straddling_the_tail_cut_is_still_masked(self) -> None:
        """Masking runs before the cut, so half a secret cannot survive it."""
        output = "x" * 40 + "Authorization: Bearer secretvalue1234567890"

        tail = redacted_tail(output, limit=20)

        assert tail is not None
        assert "secretvalue" not in tail
        assert len(tail) <= 20

    def test_no_output_records_no_tail(self) -> None:
        assert redacted_tail("", limit=20) is None

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
