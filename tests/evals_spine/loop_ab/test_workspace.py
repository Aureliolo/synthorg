# module-kind: tests
"""Provisioning a cell's workspace from the brief that declares it.

The reset, the containment guard and the per-key isolation are the recording
spine's and are covered in ``tests/evals_spine/harness/test_workspace.py``. What
is here is what the brief adds: which key names the tree, which fixture is
copied, and the one shape the generic seeder cannot express, a brief that
declares no workspace at all.
"""

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.errors import WorkspaceSeedNotFoundError, WorkspaceSpecMissingError
from evals.loop_ab.workspace import seed_brief_workspace
from evals.models.brief import (
    Brief,
    BriefKind,
    ExecutableChecks,
    HiddenCheckSpec,
    LimitsSpec,
    WorkspaceSpec,
)
from evals.runner.execution import EVAL_TASK_PROJECT
from synthorg.core.types import NotBlankStr
from synthorg.tools.sandbox.docker_sandbox import DockerSandbox

pytestmark = pytest.mark.unit

_SEED_DIR = "seeds/widget"


def _brief(*, brief_id: str = "loop-ab-simple", workspace: bool = True) -> Brief:
    """Build a minimal executable brief, optionally workspace-graded.

    Returns:
        The brief.
    """
    return Brief(
        brief_id=NotBlankStr(brief_id),
        schema_version=1,
        kind=BriefKind.EXECUTABLE,
        title=NotBlankStr("widget"),
        description=NotBlankStr("Build the widget."),
        estimated_complexity=1,
        acceptance_criteria=(NotBlankStr("The widget imports."),),
        limits=LimitsSpec(max_total_cost=1.0, max_wall_clock_seconds=60, max_turns=4),
        checks=ExecutableChecks(
            hidden_tests=(
                HiddenCheckSpec(cmd=(NotBlankStr("echo"), NotBlankStr("ok"))),
            )
        ),
        workspace=WorkspaceSpec(seed_dir=NotBlankStr(_SEED_DIR)) if workspace else None,
    )


@pytest.fixture
def suite_root(tmp_path: Path) -> Path:
    """A suite directory carrying a seed fixture with a nested subdirectory.

    Returns:
        The suite root.
    """
    seed = tmp_path / "suite" / _SEED_DIR
    (seed / "pkg").mkdir(parents=True)
    (seed / "README.md").write_text("seed readme\n", encoding="utf-8")
    (seed / "pkg" / "widget.py").write_text("VALUE = 1\n", encoding="utf-8")
    return tmp_path / "suite"


def test_the_brief_fixture_is_copied_whole(suite_root: Path, tmp_path: Path) -> None:
    """The loop starts from a faithful copy of the committed fixture."""
    cell = seed_brief_workspace(
        brief=_brief(), suite_root=suite_root, work_root=tmp_path / "work"
    )

    project = cell.project_dir
    assert (project / "README.md").read_text(encoding="utf-8") == "seed readme\n"
    assert (project / "pkg" / "widget.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_the_sandbox_binds_the_project_subtree_the_seed_landed_in(
    suite_root: Path, tmp_path: Path
) -> None:
    """A run's project id must resolve to the graded directory.

    ``AgentEngine.run`` binds the task's project into the correlation context,
    which both the shell sandbox and the OpenHands sandbox read to pick their
    mount. A layout the mount cannot resolve fails every cell at the first tool
    call, so the two are asserted together rather than assumed to agree.
    """
    cell = seed_brief_workspace(
        brief=_brief(), suite_root=suite_root, work_root=tmp_path / "work"
    )
    sandbox = DockerSandbox(workspace=cell.root)

    resolved = asyncio.run(sandbox._project_root(EVAL_TASK_PROJECT))

    assert resolved == cell.project_dir.resolve()


def test_each_brief_gets_its_own_workspace(suite_root: Path, tmp_path: Path) -> None:
    """The brief id, not something else, is the key naming a cell's tree."""
    work_root = tmp_path / "work"

    first = seed_brief_workspace(
        brief=_brief(brief_id="brief-one"), suite_root=suite_root, work_root=work_root
    )
    second = seed_brief_workspace(
        brief=_brief(brief_id="brief-two"), suite_root=suite_root, work_root=work_root
    )

    assert first.root.name == "brief-one"
    assert second.root.name == "brief-two"


def test_missing_seed_fixture_fails_loud(tmp_path: Path) -> None:
    """A brief pointing at an absent fixture must not silently run empty."""
    (tmp_path / "suite").mkdir()

    with pytest.raises(WorkspaceSeedNotFoundError, match=_SEED_DIR):
        seed_brief_workspace(
            brief=_brief(), suite_root=tmp_path / "suite", work_root=tmp_path / "work"
        )


def test_brief_without_a_workspace_block_is_refused(
    suite_root: Path, tmp_path: Path
) -> None:
    """Calling the workspace path for a text-deliverable brief is a contract error."""
    with pytest.raises(WorkspaceSpecMissingError, match="loop-ab-simple"):
        seed_brief_workspace(
            brief=_brief(workspace=False),
            suite_root=suite_root,
            work_root=tmp_path / "work",
        )


def test_brief_id_that_escapes_the_work_root_is_refused_at_the_model() -> None:
    """The model boundary refuses an escaping key before the seeder sees it.

    ``brief_id`` reaches a path join from authored YAML, so the first line is
    the model. The seeder's own guard is the second, and is exercised in the
    spine's suite where a key can arrive without passing a model at all.
    """
    with pytest.raises(ValidationError):
        _brief(brief_id="../escaped")
