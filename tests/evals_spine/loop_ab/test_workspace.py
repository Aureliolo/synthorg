# module-kind: tests
"""Per-run workspace provisioning for the loop A/B harness.

Every ``(loop, tier, brief, repetition)`` cell runs against a workspace recreated
from the brief's committed seed fixture. That reset is the fair-comparison
invariant the whole scoreboard rests on: if one loop could inherit another's
artifacts, the acceptance grade would measure run order rather than the loop.

The layout is project-scoped because both sandboxes a cell drives resolve their
mount through the run's project id, so a flat workspace is one neither can bind.
"""

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.errors import (
    WorkspaceSeedNotFoundError,
    WorkspaceSpecMissingError,
)
from evals.loop_ab.workspace import CellWorkspace, seed_workspace
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
    """Build a minimal executable brief, optionally workspace-graded."""
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
    """A suite directory carrying a seed fixture with a nested subdirectory."""
    seed = tmp_path / "suite" / _SEED_DIR
    (seed / "pkg").mkdir(parents=True)
    (seed / "README.md").write_text("seed readme\n", encoding="utf-8")
    (seed / "pkg" / "widget.py").write_text("VALUE = 1\n", encoding="utf-8")
    return tmp_path / "suite"


def test_seeds_every_file_including_nested_directories(
    suite_root: Path, tmp_path: Path
) -> None:
    """The loop starts from a faithful copy of the committed fixture."""
    cell = seed_workspace(
        brief=_brief(), suite_root=suite_root, work_root=tmp_path / "work"
    )

    project = cell.project_dir
    assert (project / "README.md").read_text(encoding="utf-8") == "seed readme\n"
    assert (project / "pkg" / "widget.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_seed_lands_in_the_project_subtree_of_the_sandbox_root(
    suite_root: Path, tmp_path: Path
) -> None:
    """The graded directory is the project subtree, not the sandbox root."""
    cell = seed_workspace(
        brief=_brief(), suite_root=suite_root, work_root=tmp_path / "work"
    )

    assert cell.project_dir == cell.root / "projects" / EVAL_TASK_PROJECT
    assert cell.project_dir.is_dir()


def test_the_sandbox_binds_the_project_subtree_the_seed_landed_in(
    suite_root: Path, tmp_path: Path
) -> None:
    """A run's project id must resolve to the graded directory.

    ``AgentEngine.run`` binds the task's project into the correlation context,
    which both the shell sandbox and the OpenHands sandbox read to pick their
    mount. A layout the mount cannot resolve fails every cell at the first tool
    call, so the two are asserted together rather than assumed to agree.
    """
    cell = seed_workspace(
        brief=_brief(), suite_root=suite_root, work_root=tmp_path / "work"
    )
    sandbox = DockerSandbox(workspace=cell.root)

    resolved = asyncio.run(sandbox._project_root(EVAL_TASK_PROJECT))

    assert resolved == cell.project_dir.resolve()


def test_reseeding_discards_a_prior_run_artifacts(
    suite_root: Path, tmp_path: Path
) -> None:
    """A second run cannot inherit the first run's files or its edits."""
    brief = _brief()
    work_root = tmp_path / "work"
    first = seed_workspace(brief=brief, suite_root=suite_root, work_root=work_root)
    (first.project_dir / "pkg" / "widget.py").write_text(
        "VALUE = 999\n", encoding="utf-8"
    )
    (first.project_dir / "leftover.txt").write_text(
        "from the previous loop\n", encoding="utf-8"
    )
    # Outside the project subtree, so only a reset of the whole sandbox root
    # clears it; a loop can write here through the mount.
    (first.root / "stray.txt").write_text("outside the project\n", encoding="utf-8")

    second = seed_workspace(brief=brief, suite_root=suite_root, work_root=work_root)

    assert second == first
    assert (second.project_dir / "pkg" / "widget.py").read_text(
        encoding="utf-8"
    ) == "VALUE = 1\n"
    assert not (second.project_dir / "leftover.txt").exists()
    assert not (second.root / "stray.txt").exists()


def test_each_brief_gets_its_own_workspace(suite_root: Path, tmp_path: Path) -> None:
    """Briefs are isolated from each other under a shared work root."""
    work_root = tmp_path / "work"

    first = seed_workspace(
        brief=_brief(brief_id="brief-one"), suite_root=suite_root, work_root=work_root
    )
    second = seed_workspace(
        brief=_brief(brief_id="brief-two"), suite_root=suite_root, work_root=work_root
    )

    assert first != second
    assert isinstance(first, CellWorkspace)


def test_missing_seed_fixture_fails_loud(tmp_path: Path) -> None:
    """A brief pointing at an absent fixture must not silently run empty."""
    (tmp_path / "suite").mkdir()

    with pytest.raises(WorkspaceSeedNotFoundError, match=_SEED_DIR):
        seed_workspace(
            brief=_brief(), suite_root=tmp_path / "suite", work_root=tmp_path / "work"
        )


def test_brief_without_a_workspace_block_is_refused(
    suite_root: Path, tmp_path: Path
) -> None:
    """Calling the workspace path for a text-deliverable brief is a contract error."""
    with pytest.raises(WorkspaceSpecMissingError, match="loop-ab-simple"):
        seed_workspace(
            brief=_brief(workspace=False),
            suite_root=suite_root,
            work_root=tmp_path / "work",
        )


def test_brief_id_that_escapes_the_work_root_is_refused_at_the_model() -> None:
    """``brief_id`` reaches a path join from YAML, so an escaping value is
    rejected at the model boundary before it can ever reach the seeding join."""
    with pytest.raises(ValidationError):
        _brief(brief_id="../escaped")
