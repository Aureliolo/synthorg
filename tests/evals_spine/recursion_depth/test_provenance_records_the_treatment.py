# module-kind: tests
"""What a curve was measured against is published, not left in a log."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from evals.recursion_depth.claims import RequirementId
from evals.recursion_depth.emit import assemble_report, write_report
from evals.recursion_depth.journal import matrix_identity
from evals.recursion_depth.manifest import Arm, Independence, ModelPair
from evals.recursion_depth.models import (
    MERGE,
    CellRecord,
    LoopTreatments,
    Provenance,
    UnitRecord,
)
from synthorg.core.completion_enums import ReasoningEffort
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit

_EXECUTOR = ModelPair(
    provider="example-provider",
    model_id="example-capable-001",
    capability="capable",
    family="example-family-a",
    temperature=1.0,
    top_p=0.95,
    max_tokens=131_072,
)
_REVIEWER = ModelPair(
    provider="example-provider",
    model_id="example-expert-001",
    capability="expert",
    family="example-family-b",
    temperature=0.6,
    top_p=0.95,
    reasoning_effort=ReasoningEffort.HIGH,
    max_tokens=262_144,
)


def _measured_cell() -> CellRecord:
    """One cell that measured something, so a report may be assembled.

    Returns:
        The cell.
    """
    return CellRecord(
        depth_cap=1,
        arm=Arm.GATED,
        repetition=0,
        achieved_depth=1,
        units=(
            UnitRecord(
                unit_id=NotBlankStr("merge-1"),
                title=NotBlankStr("Assemble it"),
                kind=MERGE,
                depth=0,
                attempts=1,
                cost=1.0,
                tokens=1000,
                terminations=("completed",),
            ),
        ),
        merged_passing=(RequirementId("R01"),),
    )


def _provenance(**overrides: object) -> Provenance:
    """A stamp carrying the sweep's declared treatment.

    Returns:
        The provenance.
    """
    fields: dict[str, object] = {
        "generated_at": datetime(2026, 8, 31, tzinfo=UTC),
        "git_commit": NotBlankStr("abc1234"),
        "git_dirty": False,
        "manifest_sha256": NotBlankStr("deadbeef"),
        "spec_id": NotBlankStr("sqlcsv"),
        "requirement_count": 42,
        "executor": _EXECUTOR,
        "reviewer": _REVIEWER,
        "independence": Independence.CROSS_FAMILY,
        "sandbox_image": NotBlankStr("ghcr.io/example/sandbox@sha256:abc"),
        "loop": LoopTreatments(contract_stage=True, merge_attempts=3),
    }
    return Provenance.model_validate(fields | overrides)


class TestTheTreatmentIsPinnedIntoTheIdentity:
    """A run cannot resume into a journal recorded under other settings.

    The sampling and reasoning depth are what the models were ASKED for, so a
    curve spliced from two of them is two experiments wearing one chart. The
    identity hashes the whole stamp, and the treatment rides inside the pairs,
    so this holds without a field per dial.
    """

    def test_a_changed_executor_temperature_is_a_different_matrix(self) -> None:
        hotter = _provenance(executor=_EXECUTOR.model_copy(update={"temperature": 0.6}))

        assert matrix_identity(hotter) != matrix_identity(_provenance())

    def test_a_changed_reviewer_depth_is_a_different_matrix(self) -> None:
        shallower = _provenance(
            reviewer=_REVIEWER.model_copy(
                update={"reasoning_effort": ReasoningEffort.LOW}
            )
        )

        assert matrix_identity(shallower) != matrix_identity(_provenance())

    def test_a_changed_sandbox_image_is_a_different_matrix(self) -> None:
        # A sweep runs agent-authored code and grades it by importing it, so
        # which build of that image was in force is part of what the curve was
        # measured against.
        elsewhere = _provenance(
            sandbox_image=NotBlankStr("ghcr.io/example/sandbox@sha256:def")
        )

        assert matrix_identity(elsewhere) != matrix_identity(_provenance())

    def test_the_loop_the_run_actually_ran_is_a_different_matrix(self) -> None:
        """The treatment is a FLAG, so the manifest's digest cannot see it.

        ``--contract-stage`` and its opposite leave the file alone by design,
        so before this the two arms of the experiment stamped byte-identical
        headers and a resume of one inside the other's directory was accepted.
        """
        bare = _provenance(loop=LoopTreatments(contract_stage=False, merge_attempts=3))

        assert matrix_identity(bare) != matrix_identity(_provenance())

    def test_a_changed_repair_budget_is_a_different_matrix(self) -> None:
        # Equal attempts across arms is what makes them comparable at all, so
        # a cell recorded under a different budget is not the same experiment.
        once = _provenance(loop=LoopTreatments(contract_stage=True, merge_attempts=1))

        assert matrix_identity(once) != matrix_identity(_provenance())

    def test_the_same_treatment_is_the_same_matrix(self) -> None:
        # The complement, or the ones above would pass on any two stamps.
        assert matrix_identity(_provenance()) == matrix_identity(_provenance())


class TestTheReportPublishesTheTreatment:
    """A reader holding the curve can see what was asked of the models."""

    def test_both_pairs_sampling_reaches_the_markdown(self, tmp_path: Path) -> None:
        report = assemble_report(
            provenance=_provenance(),
            cells=[_measured_cell()],
            caveats=[],
            planned_cells=1,
        )

        written = write_report(report, tmp_path)
        markdown = next(path for path in written if path.suffix == ".md").read_text(
            encoding="utf-8"
        )

        assert "temperature 1.0, top_p 0.95" in markdown
        assert "reasoning_effort high" in markdown
        assert "ghcr.io/example/sandbox@sha256:abc" in markdown
        # The arm, which the manifest digest cannot carry: both treatments are
        # per-run flags that leave the file alone, so without this line two
        # arms publish identical provenance blocks.
        assert "contract stage on, 3 merge attempt(s)" in markdown

    def test_an_unstated_dial_is_named_rather_than_omitted(self) -> None:
        """An absent dial is reported as unset, never as a provider default.

        Those are different claims and only one of them is true: three of
        these four resolve to a value this system supplies (the roster
        substitutes a response ceiling, ``ModelConfig`` supplies a
        temperature, the completion config supplies a nucleus threshold), so
        calling an absence "provider defaults" asserts something no request
        carried. Omitting the dial entirely makes the same claim by silence,
        which is why every one is named.
        """
        bare = ModelPair(
            provider="example-provider",
            model_id="example-capable-001",
            capability="capable",
        )

        summary = bare.sampling_summary

        assert summary == (
            "temperature unset, top_p unset, reasoning_effort unset, max_tokens unset"
        )
        assert "default" not in summary

    def test_a_stated_dial_reads_beside_an_unstated_one(self) -> None:
        """The mixed case is the one a real pair is in."""
        half = ModelPair(
            provider="example-provider",
            model_id="example-capable-001",
            capability="capable",
            temperature=0.7,
            max_tokens=131_072,
        )

        assert half.sampling_summary == (
            "temperature 0.7, top_p unset, reasoning_effort unset, max_tokens 131072"
        )
