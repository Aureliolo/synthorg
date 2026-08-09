"""The fine-tune image's build trigger covers what the image contains.

``docker/fine-tune/Dockerfile`` copies the whole ``src/`` tree, so the
trigger watches the whole tree. A narrower glob would have to name the
entrypoint's import closure, and that closure cannot be derived safely:
Python executes every ancestor package ``__init__`` on the way to a
submodule, and this tree's package hubs import eagerly, so the closure
reaches most of the central packages rather than the one it is written
from.

The trigger gates a pull request's build and vulnerability scan, not
whether the published image is current. On a push to main the ``changes``
job does not run at all, and the build job's ``changes.result ==
'skipped'`` arm makes it build and publish unconditionally.
"""

import tomllib
from pathlib import Path
from typing import Final

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
_WORKFLOW: Final[Path] = _REPO_ROOT / ".github" / "workflows" / "build-images.yml"

#: The image's ``ENTRYPOINT`` module, run as ``python -m``.
_ENTRYPOINT_MODULE: Final[str] = "synthorg.memory.embedding.fine_tune_runner"

#: The ``dorny/paths-filter`` key whose globs gate the fine-tune build.
_FILTER_NAME: Final[str] = "fine-tune"

#: What the Dockerfile copies, and so what the trigger has to watch.
_SOURCE_TREE_GLOB: Final[str] = "src/**"

#: The job whose trigger this module is about.
_BUILD_JOB: Final[str] = "build-fine-tune-base"

#: The arm of that job's condition that makes a main push unconditional.
_UNCONDITIONAL_ON_MAIN: Final[str] = "needs.changes.result == 'skipped'"


def _build_job_condition() -> str:
    """Read the fine-tune base build job's ``if:`` expression.

    Returns:
        The condition deciding whether the job runs.
    """
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    condition = workflow["jobs"][_BUILD_JOB]["if"]
    assert isinstance(condition, str)
    return condition


def _fine_tune_filters() -> tuple[str, ...]:
    """Read the fine-tune path filter out of the images workflow.

    Returns:
        The glob patterns gating a fine-tune rebuild.
    """
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    for step in workflow["jobs"]["changes"]["steps"]:
        raw = step.get("with", {}).get("filters")
        if raw is None:
            continue
        return tuple(yaml.safe_load(raw)[_FILTER_NAME])
    msg = "No paths-filter step found in the changes job"
    raise AssertionError(msg)


class TestFineTuneRebuildTrigger:
    """The trigger covers everything the image ships."""

    def test_entrypoint_matches_the_dockerfile(self) -> None:
        dockerfile = (_REPO_ROOT / "docker" / "fine-tune" / "Dockerfile").read_text(
            encoding="utf-8",
        )

        assert _ENTRYPOINT_MODULE in dockerfile

    def test_the_whole_copied_tree_is_watched(self) -> None:
        """A narrower glob would under-cover what ``COPY src/ src/`` ships."""
        filters = _fine_tune_filters()

        assert _SOURCE_TREE_GLOB in filters, (
            "The Dockerfile copies all of src/, so a change anywhere under it "
            "changes the image. Watching a subset means a pull request can "
            f"alter the image with no build and no scan: {filters}"
        )

    def test_dependency_changes_rebuild_the_image(self) -> None:
        """The lock pins what the image installs, so it gates a rebuild."""
        filters = _fine_tune_filters()

        assert "uv.lock" in filters
        assert "pyproject.toml" in filters

    def test_a_main_push_builds_without_consulting_the_filter(self) -> None:
        """The filter scopes pull-request scanning, never image freshness.

        ``changes`` is ``pull_request``-only, so on main it is skipped and
        this arm carries the job. Losing it would make the filter decide
        whether main publishes at all, and a miss would then ship a stale
        image rather than skip a scan.
        """
        assert _UNCONDITIONAL_ON_MAIN in _build_job_condition()

    def test_the_variants_the_filter_serves_still_exist(self) -> None:
        """The two build-arg variants the image ships are declared groups."""
        pyproject = tomllib.loads(
            (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"),
        )
        groups = pyproject["dependency-groups"]

        assert "fine-tune-gpu" in groups
        assert "fine-tune-cpu" in groups
