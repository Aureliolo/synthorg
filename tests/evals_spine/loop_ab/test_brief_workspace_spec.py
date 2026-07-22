# module-kind: tests
"""Schema coverage for the workspace-graded brief block.

A workspace-graded brief hands the execution loop a real writable directory
seeded from a committed fixture; the loop writes files itself and the grader
runs the brief's checks against that directory. The seed path is authored in
YAML, so it is validated at the file boundary: it must stay relative and inside
the suite, and it is only meaningful for an executable brief.
"""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from evals.loader.briefs import load_brief_suite
from evals.models.brief import Brief, BriefKind, WorkspaceSpec
from tests.evals_spine.conftest import BriefYamlWriter

pytestmark = pytest.mark.unit


def test_relative_seed_dir_is_accepted() -> None:
    """A plain relative seed directory is the ordinary authoring shape."""
    spec = WorkspaceSpec(seed_dir="seeds/loop-ab-simple")

    assert spec.seed_dir == "seeds/loop-ab-simple"


@pytest.mark.parametrize(
    "seed_dir",
    [
        "/etc/passwd",
        "\\\\server\\share",
        "C:/Windows",
        "C:\\Windows",
    ],
)
def test_absolute_seed_dir_is_rejected(seed_dir: str) -> None:
    """An absolute seed path would read outside the suite; refuse at load."""
    with pytest.raises(ValidationError, match="must be a relative path"):
        WorkspaceSpec(seed_dir=seed_dir)


@pytest.mark.parametrize(
    "seed_dir",
    [
        "../outside",
        "seeds/../../outside",
        "seeds/..",
    ],
)
def test_parent_traversal_in_seed_dir_is_rejected(seed_dir: str) -> None:
    """A ``..`` segment escapes the suite root even while staying relative."""
    with pytest.raises(ValidationError, match="parent-directory segment"):
        WorkspaceSpec(seed_dir=seed_dir)


def test_workspace_is_rejected_on_a_judged_brief(
    write_brief_yaml: BriefYamlWriter,
) -> None:
    """Only an executable brief has checks to run against a workspace."""
    path = write_brief_yaml(
        "judged.yaml",
        "judged",
        workspace={"seed_dir": "seeds/x"},
    )
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    with pytest.raises(ValidationError, match="must not carry a 'workspace' block"):
        Brief.model_validate(raw)


def test_executable_brief_loads_its_workspace_block(
    write_brief_yaml: BriefYamlWriter, tmp_path: Path
) -> None:
    """A workspace-graded brief round-trips through the real suite loader."""
    write_brief_yaml(
        "exec.yaml",
        "executable",
        workspace={"seed_dir": "seeds/loop-ab-simple"},
    )

    briefs = load_brief_suite(tmp_path)

    assert len(briefs) == 1
    brief = briefs[0]
    assert brief.kind is BriefKind.EXECUTABLE
    assert brief.workspace is not None
    assert brief.workspace.seed_dir == "seeds/loop-ab-simple"


def test_executable_brief_without_workspace_still_validates(
    write_brief_yaml: BriefYamlWriter, tmp_path: Path
) -> None:
    """The block is additive: the existing text-deliverable briefs are unaffected."""
    write_brief_yaml("exec.yaml", "executable")

    briefs = load_brief_suite(tmp_path)

    assert briefs[0].workspace is None
