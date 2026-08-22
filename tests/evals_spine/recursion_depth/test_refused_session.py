# module-kind: tests
"""A session that never ran is not a unit that failed to deliver.

The two are different facts about different subsystems, and the survival metric
is read by somebody deciding whether recursion held up. A live run recorded
three consecutive leaves at zero turns and zero tokens, every one of them saying
the agent had written no files, when what had actually happened was upstream:
nothing was ever asked to build anything.
"""

from datetime import date
from pathlib import Path

import pytest

from evals.harness.workspace import CellWorkspace
from evals.recursion_depth import execute as execute_module
from evals.recursion_depth import gate as gate_module
from evals.recursion_depth import merge as merge_module
from evals.recursion_depth.execute import run_leaf
from evals.recursion_depth.gate import BlindMergeReviewer
from evals.recursion_depth.manifest import ModelPair
from evals.recursion_depth.merge import MergePlan, run_merge
from evals.recursion_depth.session import SessionLimits, SessionOutcome, SweepDeps
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.unit

_PAIR = ModelPair(
    provider=NotBlankStr("example-provider"),
    model_id=NotBlankStr("example-capable-001"),
    capability="capable",
    family=NotBlankStr("example-family-a"),
)

_LIMITS = SessionLimits(max_turns=4, cost_ceiling=1.0, token_ceiling=1000)


def _task(title: str) -> Task:
    """Build a task the harness can brief.

    Returns:
        The task.
    """
    return Task(
        id=as_uuid(f"task:{title}"),
        title=NotBlankStr(title),
        description=NotBlankStr(f"Do {title}."),
        type=TaskType.DEVELOPMENT,
        priority=Priority.HIGH,
        project=NotBlankStr(sid("project:recursion-depth-refused")),
        created_by=NotBlankStr("test"),
        status=TaskStatus.CREATED,
        acceptance_criteria=(AcceptanceCriterion(description=NotBlankStr("It runs")),),
    )


def _identity() -> AgentIdentity:
    """Build the agent a refused session would have run as.

    Returns:
        The identity.
    """
    return AgentIdentity(
        id=as_uuid("identity:builder"),
        name=NotBlankStr("Builder"),
        role=NotBlankStr("Developer"),
        department=NotBlankStr("Engineering"),
        model=ModelConfig(
            provider=_PAIR.provider, model_id=_PAIR.model_id, capability="capable"
        ),
        hiring_date=date(2026, 1, 1),
    )


def _workspace(tmp_path: Path, name: str) -> CellWorkspace:
    """Build a workspace whose project directory exists.

    Returns:
        The workspace.
    """
    workspace = CellWorkspace(root=tmp_path / name)
    workspace.project_dir.mkdir(parents=True, exist_ok=True)
    return workspace


def _deps() -> SweepDeps:
    """Build deps whose factories must not be reached.

    A refused session is decided before any grading, so a factory that runs
    here is the defect rather than the setup.

    Returns:
        The deps.
    """

    async def _no_provider(_binding: object) -> object:
        raise AssertionError

    def _no_sandbox(_root: Path) -> object:
        raise AssertionError

    def _no_grader(_workspace: object) -> object:
        raise AssertionError

    return SweepDeps(
        build_provider=_no_provider,  # type: ignore[arg-type]
        build_tool_registry=lambda _workspace: None,
        build_grader=_no_grader,  # type: ignore[arg-type]
        build_sandbox=_no_sandbox,  # type: ignore[arg-type]
    )


def _refusing(monkeypatch: pytest.MonkeyPatch, module: object) -> None:
    """Make *module*'s session runner return without taking a turn.

    Args:
        monkeypatch: The patcher.
        module: The module whose ``run_session`` name is replaced.
    """

    async def _refused(_deps: SweepDeps, **_rest: object) -> SessionOutcome:
        return SessionOutcome(
            cost=0.0, tokens=0, turns=0, termination="provider_unavailable"
        )

    monkeypatch.setattr(module, "run_session", _refused)


class TestALeafThatNeverRan:
    """Zero turns and zero tokens is upstream, not the agent's work."""

    async def test_it_is_not_reported_as_missing_artifacts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _refusing(monkeypatch, execute_module)

        outcome = await run_leaf(
            _deps(),
            task=_task("Build the parser"),
            owner=_identity(),
            workspace=_workspace(tmp_path, "leaf"),
            execution_id="d1-gated-r0-leaf",
            limits=_LIMITS,
        )

        assert not outcome.delivered
        assert "declared artifacts" not in outcome.detail
        assert "ran no turns" in outcome.detail

    async def test_it_names_how_the_session_terminated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The one fact that separates a spent quota from a refused credential
        # from an unreachable endpoint, and it survives nowhere else.
        _refusing(monkeypatch, execute_module)

        outcome = await run_leaf(
            _deps(),
            task=_task("Build the parser"),
            owner=_identity(),
            workspace=_workspace(tmp_path, "leaf"),
            execution_id="d1-gated-r0-leaf",
            limits=_LIMITS,
        )

        assert "provider_unavailable" in outcome.detail


class TestAMergeThatNeverRan:
    """Every attempt refused is not an assembly that went wrong."""

    async def test_it_is_not_reported_as_missing_artifacts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _refusing(monkeypatch, merge_module)
        # The blind reviewer spends the same budget as the gated one, so it
        # runs a session of its own and is refused alongside the assembly.
        _refusing(monkeypatch, gate_module)
        plan = MergePlan(
            task=_task("Assemble it"),
            owner=_identity(),
            workspace=_workspace(tmp_path, "merge"),
            pieces=(),
            criteria=(NotBlankStr("It runs"),),
            attempts=2,
            limits=_LIMITS,
            execution_prefix="d1-gated-r0-merge",
        )

        outcome = await run_merge(
            _deps(), plan=plan, reviewer=BlindMergeReviewer(deps=_deps())
        )

        assert not outcome.delivered
        assert "declared artifacts" not in outcome.detail
        assert "no assembly attempt ran a single turn" in outcome.detail
