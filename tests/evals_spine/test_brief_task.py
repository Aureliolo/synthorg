# module-kind: tests
"""The task a brief becomes, and which briefs arm the zero-artifact guard.

Both loops reclassify a COMPLETED run that called no tool into ``NO_OP`` only
when the task declares expected artifacts. A brief that declares them and a task
that does not is the guard silently disarmed: a loop that wrote nothing scores as
a clean success, and the A/B's NO_OP rate reads zero because nothing could ever
raise it.

The gate is the workspace block, not the artifact list. A workspace-graded brief
hands the loop a real directory and grades what it left there, so its declared
artifacts are the loop's own output. Every other kind has its deliverable text
materialised into files by the runner afterwards, so the same declaration
describes something the loop was never asked to produce.
"""

from pathlib import Path

import pytest

from evals.loader.briefs import load_brief_suite
from evals.models.brief import (
    ArtifactSpec,
    Brief,
    BriefKind,
    ExecutableChecks,
    HiddenCheckSpec,
    LimitsSpec,
    WorkspaceSpec,
)
from evals.runner.execution import _brief_task, wall_clock_events
from evals.scoring.penalties import PENALTY_CLASS_BRIEF_WALL_CLOCK
from synthorg.core.artifact import ArtifactType
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit

_AGENT_ID = "00000000-0000-4000-8000-00000000ab00"
_SUITE = Path(__file__).resolve().parents[2] / "evals" / "loop_ab" / "briefs"
_CHECKS = ExecutableChecks(
    hidden_tests=(HiddenCheckSpec(cmd=(NotBlankStr("echo"), NotBlankStr("ok"))),)
)


def _brief(*, workspace: WorkspaceSpec | None, artifacts: tuple[str, ...]) -> Brief:
    """A minimal executable brief, optionally workspace-graded.

    Returns:
        The brief under test.
    """
    return Brief(
        brief_id=NotBlankStr("brief-under-test"),
        schema_version=1,
        kind=BriefKind.EXECUTABLE,
        title=NotBlankStr("title"),
        description=NotBlankStr("description"),
        estimated_complexity=1,
        expected_artifacts=tuple(
            ArtifactSpec(kind="file", path=NotBlankStr(path)) for path in artifacts
        ),
        acceptance_criteria=(NotBlankStr("it works"),),
        limits=LimitsSpec(max_total_cost=1.0, max_wall_clock_seconds=60, max_turns=4),
        checks=_CHECKS,
        workspace=workspace,
    )


class TestExpectedArtifacts:
    def test_a_workspace_graded_brief_arms_the_zero_artifact_guard(self) -> None:
        brief = _brief(
            workspace=WorkspaceSpec(seed_dir=NotBlankStr("seeds/x")),
            artifacts=("textkit.py", "ledger/accounts.py"),
        )

        task = _brief_task(brief, agent_id=_AGENT_ID)

        assert [artifact.path for artifact in task.artifacts_expected] == [
            "textkit.py",
            "ledger/accounts.py",
        ]

    def test_a_brief_the_runner_materialises_for_declares_nothing(self) -> None:
        # The deliverable text is written into these paths by the runner after
        # the run, so declaring them on the task would demand of the loop
        # something the harness itself produces. Every judged brief in the
        # golden-company suite runs against a scripted provider that calls no
        # tool, so an ungated map would reclassify all of them as NO_OP.
        brief = _brief(workspace=None, artifacts=("solution.py",))

        task = _brief_task(brief, agent_id=_AGENT_ID)

        assert task.artifacts_expected == ()

    def test_a_workspace_brief_declaring_none_stays_disarmed(self) -> None:
        brief = _brief(
            workspace=WorkspaceSpec(seed_dir=NotBlankStr("seeds/x")), artifacts=()
        )

        task = _brief_task(brief, agent_id=_AGENT_ID)

        assert task.artifacts_expected == ()

    def test_every_shipped_ab_brief_arms_the_guard(self) -> None:
        # The A/B's own suite is the one that has to work: a brief here whose
        # artifacts never reached its task would be measured with the NO_OP
        # rule switched off, and the scoreboard would report a rate of zero for
        # a check that never ran.
        for brief in load_brief_suite(_SUITE):
            task = _brief_task(brief, agent_id=_AGENT_ID)
            assert task.artifacts_expected, (
                f"brief {brief.brief_id!r} does not arm the zero-artifact guard"
            )


class TestAcceptanceCriteria:
    def test_the_brief_criteria_reach_the_task(self) -> None:
        # ``prompt_render`` puts ``task.acceptance_criteria`` in front of the
        # agent, so a brief that declares what "done" means while the task
        # carries none measures a loop working from strictly less than a real
        # task gives it.
        brief = _brief(
            workspace=WorkspaceSpec(seed_dir=NotBlankStr("seeds/x")),
            artifacts=("textkit.py",),
        )

        task = _brief_task(brief, agent_id=_AGENT_ID)

        assert [c.description for c in task.acceptance_criteria] == ["it works"]

    def test_every_shipped_ab_brief_states_its_criteria_on_the_task(self) -> None:
        # Compared by text and in order, not by count: the agent reads these,
        # so the same number of different sentences is the failure this would
        # otherwise pass.
        for brief in load_brief_suite(_SUITE):
            task = _brief_task(brief, agent_id=_AGENT_ID)
            assert [c.description for c in task.acceptance_criteria] == list(
                brief.acceptance_criteria
            ), f"brief {brief.brief_id!r} does not state its criteria on the task"


class TestWallClockBudget:
    def test_a_run_inside_its_budget_reports_nothing(self) -> None:
        brief = _brief(workspace=None, artifacts=())

        assert wall_clock_events(59.0, brief=brief) == {}

    def test_a_run_exactly_at_its_budget_is_not_over_it(self) -> None:
        # A budget is what the run is allowed to take, so spending all of it is
        # compliance rather than a breach.
        brief = _brief(workspace=None, artifacts=())

        assert wall_clock_events(60.0, brief=brief) == {}

    def test_a_run_past_its_budget_is_reported(self) -> None:
        # The class this raises has been in the penalty table with nothing to
        # emit it, so a brief could overrun its declared time and no scorecard
        # or scoreboard would ever say so.
        brief = _brief(workspace=None, artifacts=())

        assert wall_clock_events(60.1, brief=brief) == {
            PENALTY_CLASS_BRIEF_WALL_CLOCK: 1
        }


class TestArtifactTypes:
    def test_a_report_maps_to_documentation_and_the_rest_to_code(self) -> None:
        # The loops gate on presence rather than on the type, so this is a
        # labelling choice rather than a load-bearing one; it is asserted so the
        # label stays deliberate rather than whatever the first branch returned.
        brief = Brief(
            brief_id=NotBlankStr("brief-under-test"),
            schema_version=1,
            kind=BriefKind.EXECUTABLE,
            title=NotBlankStr("title"),
            description=NotBlankStr("description"),
            estimated_complexity=1,
            expected_artifacts=(
                ArtifactSpec(kind="report", path=NotBlankStr("REPORT.md")),
                ArtifactSpec(kind="file", path=NotBlankStr("mod.py")),
                ArtifactSpec(kind="dir", path=NotBlankStr("pkg")),
                ArtifactSpec(kind="diff", path=NotBlankStr("change.patch")),
            ),
            acceptance_criteria=(NotBlankStr("it works"),),
            limits=LimitsSpec(
                max_total_cost=1.0, max_wall_clock_seconds=60, max_turns=4
            ),
            checks=_CHECKS,
            workspace=WorkspaceSpec(seed_dir=NotBlankStr("seeds/x")),
        )

        task = _brief_task(brief, agent_id=_AGENT_ID)

        assert [artifact.type for artifact in task.artifacts_expected] == [
            ArtifactType.DOCUMENTATION,
            ArtifactType.CODE,
            ArtifactType.CODE,
            ArtifactType.CODE,
        ]
