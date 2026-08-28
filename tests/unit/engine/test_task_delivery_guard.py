"""Did this run deliver what it promised, and does it say so when it cannot?

The guard is the only thing between a work task that produced nothing and a
review that reads it as done, so both of its answers matter: the refusals it
issues, and the cases where it cannot ask the question at all. The second set
has no verdict to assert on, only a line saying the run reached review
unverified, which is the whole reason those branches are not silent.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import structlog

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.artifact import ArtifactType, ExpectedArtifact
from synthorg.core.completion_enums import FinishReason
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus, TaskType
from synthorg.engine.artifacts.baseline_scope import (
    RunBaseline,
    RunBaselineProbe,
    run_baseline_scope,
)
from synthorg.engine.artifacts.expected_artifact_check import ArtifactPresence
from synthorg.engine.artifacts.workspace_fingerprint import fingerprint_tree
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.engine.resume_scope import resumed_run_scope
from synthorg.engine.task_delivery_guard import (
    EMPTY_RUN_REASON,
    MISSING_ARTIFACTS_REASON,
    NO_OP_JUSTIFICATION_KEY,
    NOTHING_PRODUCED_REASON,
    UNCHANGED_ARTIFACTS_REASON,
    _absent_artifacts,
    no_delivery_reason,
)
from synthorg.engine.task_execution import TaskExecution
from synthorg.execution.turn import TurnRecord
from synthorg.observability.events.execution import (
    EXECUTION_ENGINE_ARTIFACT_PROBE_DEGRADED,
)

pytestmark = pytest.mark.unit

_DECLARED = "src/board.ts"


def _task(*, declares: bool = True) -> Task:
    return Task(
        title="Scaffold the board",
        description="Draw the grid",
        type=TaskType.DEVELOPMENT,
        project="proj-tetris",
        created_by="Ada Chen",
        artifacts_expected=(
            (ExpectedArtifact(type=ArtifactType.CODE, path=_DECLARED),)
            if declares
            else ()
        ),
    )


def _context(task: Task | None) -> AgentContext:
    return AgentContext(
        execution_id="exec-1",
        identity=AgentIdentity(
            name="Ada Chen",
            role="developer",
            department="engineering",
            model=ModelConfig(provider="test-provider", model_id="test-basic-001"),
            hiring_date=date(2026, 1, 1),
        ),
        task_execution=(
            None
            if task is None
            else TaskExecution(task=task, status=TaskStatus.IN_PROGRESS)
        ),
        started_at=datetime.now(UTC),
    )


def _run(
    context: AgentContext,
    *,
    tool_calls: int = 0,
    metadata: dict[str, object] | None = None,
) -> ExecutionResult:
    return ExecutionResult(
        context=context,
        termination_reason=TerminationReason.COMPLETED,
        turns=tuple(
            TurnRecord(
                turn_number=index + 1,
                input_tokens=1,
                output_tokens=1,
                cost=0.0,
                tool_calls_made=("read_file",),
                finish_reason=FinishReason.TOOL_USE,
            )
            for index in range(tool_calls)
        ),
        metadata=metadata or {},
    )


def _probe(
    presence: ArtifactPresence, *, workspace: Path = Path("unbuilt")
) -> RunBaselineProbe:
    """Return a probe answering *presence* whatever it is asked.

    Returns:
        The probe callable.
    """

    async def _ask(
        project_id: str, expected: Sequence[ExpectedArtifact]
    ) -> RunBaseline:
        """Answer the fixed presence.

        Returns:
            A baseline carrying the presence this probe was built with.
        """
        del project_id, expected
        return RunBaseline(
            workspace=workspace,
            declared=presence,
            tree=fingerprint_tree(workspace),
        )

    return _ask


def _baseline(
    presence: ArtifactPresence, *, workspace: Path = Path("unbuilt")
) -> RunBaseline:
    """Take a baseline the way the engine does at run start.

    Returns:
        The baseline.
    """
    return RunBaseline(
        workspace=workspace, declared=presence, tree=fingerprint_tree(workspace)
    )


def _degradations(logs: Sequence[Mapping[str, object]]) -> list[object]:
    return [
        log.get("reason")
        for log in logs
        if log.get("event") == EXECUTION_ENGINE_ARTIFACT_PROBE_DEGRADED
    ]


class TestRefusals:
    async def test_an_empty_work_run_fails(self) -> None:
        ctx = _context(_task())

        verdict = await no_delivery_reason(_run(ctx), ctx, run_probe=None)

        assert verdict == EMPTY_RUN_REASON

    async def test_a_recorded_no_op_justification_passes(self) -> None:
        # The one sanctioned way to finish empty-handed, and it answers every
        # question below rather than only the turn count.
        ctx = _context(_task())
        run = _run(ctx, metadata={NO_OP_JUSTIFICATION_KEY: "nothing to change"})

        assert await no_delivery_reason(run, ctx, run_probe=None) is None

    async def test_a_task_declaring_nothing_is_not_probed(self) -> None:
        ctx = _context(_task(declares=False))
        run = _run(ctx, tool_calls=1)

        assert await no_delivery_reason(run, ctx, run_probe=None) is None

    async def test_a_run_that_produced_none_of_its_declarations_fails(self) -> None:
        # The case the tool-call proxy waves through: the agent read files,
        # wrote nothing, and stopped.
        ctx = _context(_task())
        run = _run(ctx, tool_calls=3)
        probe = _probe(ArtifactPresence(probed=(_DECLARED,), missing=(_DECLARED,)))

        verdict = await no_delivery_reason(run, ctx, run_probe=probe)

        assert verdict == MISSING_ARTIFACTS_REASON.format(paths=_DECLARED)

    async def test_a_run_that_changed_nothing_it_declared_fails(self) -> None:
        # Presence alone answers a task that creates. A task that edits found
        # its declaration already there, so only the baseline separates the
        # run that fixed the file from the one that read it and stopped.
        ctx = _context(_task())
        run = _run(ctx, tool_calls=2)
        unchanged = ArtifactPresence(probed=(_DECLARED,), digests={_DECLARED: "abc"})

        with run_baseline_scope(_baseline(unchanged)):
            verdict = await no_delivery_reason(run, ctx, run_probe=_probe(unchanged))

        assert verdict == UNCHANGED_ARTIFACTS_REASON.format(paths=_DECLARED)

    async def test_a_run_that_changed_what_it_declared_passes(
        self, tmp_path: Path
    ) -> None:
        ctx = _context(_task())
        run = _run(ctx, tool_calls=2)
        declared = tmp_path / _DECLARED
        declared.parent.mkdir(parents=True)
        declared.write_text("// first\n", encoding="utf-8")
        before = ArtifactPresence(probed=(_DECLARED,), digests={_DECLARED: "abc"})
        after = ArtifactPresence(probed=(_DECLARED,), digests={_DECLARED: "def"})
        baseline = _baseline(before, workspace=tmp_path)
        declared.write_text("// rewritten, and longer\n", encoding="utf-8")

        with run_baseline_scope(baseline):
            verdict = await no_delivery_reason(
                run, ctx, run_probe=_probe(after, workspace=tmp_path)
            )

        assert verdict is None

    async def test_a_same_length_edit_to_a_declaration_passes(
        self, tmp_path: Path
    ) -> None:
        """The two workspace questions read different evidence.

        The tree is compared by size and a declaration by digest, so an edit
        that keeps a file's length leaves the tree fingerprint identical while
        the digest proves the run rewrote exactly what it promised. Flipping a
        constant and correcting an identifier are both ordinary work of that
        shape, and the coarser signal must not fail them.
        """
        ctx = _context(_task())
        run = _run(ctx, tool_calls=2)
        declared = tmp_path / _DECLARED
        declared.parent.mkdir(parents=True)
        declared.write_text("const RETRIES = 1\n", encoding="utf-8")
        before = ArtifactPresence(probed=(_DECLARED,), digests={_DECLARED: "abc"})
        after = ArtifactPresence(probed=(_DECLARED,), digests={_DECLARED: "def"})
        baseline = _baseline(before, workspace=tmp_path)
        declared.write_text("const RETRIES = 5\n", encoding="utf-8")

        with run_baseline_scope(baseline):
            verdict = await no_delivery_reason(
                run, ctx, run_probe=_probe(after, workspace=tmp_path)
            )

        assert verdict is None

    async def test_a_run_that_produced_something_undeclared_reaches_review(
        self, tmp_path: Path
    ) -> None:
        """The declaration is a guess made before the tree existed.

        Measured on a live cell: three of eight units wrote 4, 8 and 10
        modules apiece under names the planner had not guessed. Failing them
        here would discard working code over a naming disagreement a reviewer
        can read; the missing declaration is the reviewer's question, not
        this guard's.
        """
        ctx = _context(_task())
        run = _run(ctx, tool_calls=9)
        absent = ArtifactPresence(probed=(_DECLARED,), missing=(_DECLARED,))
        baseline = _baseline(absent, workspace=tmp_path)
        (tmp_path / "board.tsx").write_text("// real work\n", encoding="utf-8")

        with run_baseline_scope(baseline):
            verdict = await no_delivery_reason(
                run, ctx, run_probe=_probe(absent, workspace=tmp_path)
            )

        assert verdict is None

    async def test_a_run_that_touched_nothing_anywhere_fails(
        self, tmp_path: Path
    ) -> None:
        """The only check a prose declaration ever gets.

        ``the integrated, runnable deliverable`` is not path-shaped, so it is
        never probed and the declared-artifact arms answer nothing about it.
        A run under it could finish having written no file at all.
        """
        ctx = _context(_task())
        run = _run(ctx, tool_calls=5)
        prose = ArtifactPresence()

        with run_baseline_scope(_baseline(prose, workspace=tmp_path)):
            verdict = await no_delivery_reason(
                run, ctx, run_probe=_probe(prose, workspace=tmp_path)
            )

        assert verdict == NOTHING_PRODUCED_REASON


class TestResumedRuns:
    """A continued segment carries only its own turns, and its own baseline.

    Both exemptions exist because this segment's evidence says nothing about
    what an earlier one produced before the run parked. The presence arm is
    deliberately not exempt: the filesystem has no such blind spot, so a
    resumed run that produced none of its declarations still fails.
    """

    async def test_an_empty_resumed_run_is_not_failed(self) -> None:
        ctx = _context(_task())

        with resumed_run_scope():
            verdict = await no_delivery_reason(_run(ctx), ctx, run_probe=None)

        assert verdict is None

    async def test_a_resumed_run_touching_nothing_it_declared_is_not_failed(
        self,
    ) -> None:
        # The baseline was taken at the resume, so it already holds whatever
        # an earlier segment wrote: unchanged here is not "produced nothing".
        ctx = _context(_task())
        run = _run(ctx, tool_calls=2)
        unchanged = ArtifactPresence(probed=(_DECLARED,), digests={_DECLARED: "abc"})

        with resumed_run_scope(), run_baseline_scope(_baseline(unchanged)):
            verdict = await no_delivery_reason(run, ctx, run_probe=_probe(unchanged))

        assert verdict is None

    async def test_a_resumed_run_missing_its_declarations_still_fails(self) -> None:
        ctx = _context(_task())
        run = _run(ctx, tool_calls=3)
        probe = _probe(ArtifactPresence(probed=(_DECLARED,), missing=(_DECLARED,)))

        with resumed_run_scope():
            verdict = await no_delivery_reason(run, ctx, run_probe=probe)

        assert verdict == MISSING_ARTIFACTS_REASON.format(paths=_DECLARED)


class TestUnverifiable:
    """Every route to "could not ask" names itself, or it reads as verified."""

    async def test_an_unwired_probe_is_reported(self) -> None:
        ctx = _context(_task())
        run = _run(ctx, tool_calls=2)

        with structlog.testing.capture_logs() as logs:
            verdict = await no_delivery_reason(run, ctx, run_probe=None)

        assert verdict is None
        assert _degradations(logs) == [
            "no workspace probe is wired; declared artifacts unverified"
        ]

    async def test_a_context_without_its_task_execution_is_reported(self) -> None:
        # Unreachable through ``no_delivery_reason``, which only asks about a
        # task it read the declarations off. The guard is what lets the probe
        # call be typed, and a guard that returns the same ``None`` as a
        # confirmed workspace is the shape this rules out.
        with structlog.testing.capture_logs() as logs:
            presence = await _absent_artifacts(
                _probe(ArtifactPresence()), _context(None)
            )

        assert presence is None
        assert _degradations(logs) == [
            "run carries no task execution; nothing to probe against"
        ]

    async def test_a_task_naming_no_project_is_reported(self) -> None:
        # ``Task.project`` is a ``NotBlankStr``, so validation is what keeps
        # this out of reach; the copy skips it deliberately. Without the guard
        # a blank id resolves to the shared workspace root, and every project's
        # files would answer for this one.
        detached = _context(_task()).model_copy(
            update={
                "task_execution": TaskExecution(
                    task=_task().model_copy(update={"project": "   "}),
                    status=TaskStatus.IN_PROGRESS,
                )
            }
        )

        with structlog.testing.capture_logs() as logs:
            presence = await _absent_artifacts(_probe(ArtifactPresence()), detached)

        assert presence is None
        assert _degradations(logs) == [
            "task names no project; its workspace cannot be resolved"
        ]

    async def test_an_unreadable_workspace_is_reported(self) -> None:
        ctx = _context(_task())

        async def _raising(
            project_id: str, expected: Sequence[ExpectedArtifact]
        ) -> RunBaseline:
            """Fail the way a workspace the backend cannot read does.

            Raises:
                OSError: Always.
            """
            del project_id, expected
            raise OSError(21, "Is a directory")

        with structlog.testing.capture_logs() as logs:
            verdict = await no_delivery_reason(
                _run(ctx, tool_calls=2), ctx, run_probe=_raising
            )

        assert verdict is None
        assert [log.get("event") for log in logs] == [
            EXECUTION_ENGINE_ARTIFACT_PROBE_DEGRADED
        ]
