# module-kind: tests
"""The matrix refuses a weakened judge, and the report refuses a silent gap."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from evals.errors import RecursionDepthJudgeNotIndependentError
from evals.recursion_depth.emit import write_report
from evals.recursion_depth.manifest import (
    Arm,
    Independence,
    ModelPair,
    RecursionDepthManifest,
    load_manifest,
)
from evals.recursion_depth.models import (
    LEAF,
    ORACLE_CAVEAT,
    SIZING_CAVEAT,
    CellRecord,
    DepthPoint,
    Provenance,
    RecursionDepthReport,
    UnitRecord,
)
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit

_COMMITTED_MANIFEST = (
    Path(__file__).resolve().parents[3] / "evals" / "recursion_depth" / "manifest.yaml"
)


def _manifest_payload(**overrides: object) -> dict[str, object]:
    """Build a manifest payload with the shipped defaults.

    Returns:
        The payload.
    """
    payload: dict[str, object] = {
        "spec_dir": "evals/recursion_depth/spec/sqlcsv",
        "depths": [1, 2],
        "repetitions": {1: 1, 2: 2},
        "arms": ["gated", "ungated"],
        "executor": {
            "provider": "example-provider",
            "model_id": "example-capable-001",
            "capability": "capable",
            "family": "example-family-a",
        },
        "reviewer": {
            "provider": "example-provider",
            "model_id": "example-expert-001",
            "capability": "expert",
            "family": "example-family-a",
        },
        "independence": "same_family",
        "merge_attempts": 3,
        "unit_max_turns": 40,
        "unit_cost_ceiling": 2.0,
        "unit_token_ceiling": 600000,
        "max_sessions": 100,
    }
    payload.update(overrides)
    return payload


class TestTheJudgeMustBeIndependent:
    """The gate is the treatment, so a weakened judge biases toward the null."""

    def test_the_shipped_manifest_loads(self) -> None:
        manifest = load_manifest(_COMMITTED_MANIFEST)

        assert manifest.arms == (Arm.GATED, Arm.UNGATED)
        assert manifest.reviewer != manifest.executor

    def test_an_identical_pair_is_refused(self) -> None:
        same = {
            "provider": "example-provider",
            "model_id": "example-capable-001",
            "capability": "capable",
            "family": "example-family-a",
        }

        with pytest.raises(RecursionDepthJudgeNotIndependentError, match="maximum"):
            RecursionDepthManifest.model_validate(
                _manifest_payload(executor=same, reviewer=same)
            )

    def test_claiming_cross_family_within_one_family_is_refused(self) -> None:
        with pytest.raises(
            RecursionDepthJudgeNotIndependentError, match="shared family"
        ):
            RecursionDepthManifest.model_validate(
                _manifest_payload(independence="cross_family")
            )

    def test_claiming_cross_family_without_declaring_families_is_refused(self) -> None:
        # The claim is what generates the absence of a caveat, so it may not
        # rest on nothing: a manifest that names no family has stated no reason
        # to believe the two judges are decorrelated.
        with pytest.raises(
            RecursionDepthJudgeNotIndependentError, match="rests on nothing"
        ):
            RecursionDepthManifest.model_validate(
                _manifest_payload(
                    independence="cross_family",
                    executor={
                        "provider": "example-provider",
                        "model_id": "example-capable-001",
                        "capability": "capable",
                    },
                    reviewer={
                        "provider": "other-provider",
                        "model_id": "example-expert-001",
                        "capability": "expert",
                    },
                )
            )

    def test_claiming_same_family_across_two_families_is_refused(self) -> None:
        # Refused rather than quietly upgraded: the artifact's caveat is
        # generated from the declared class, so a manifest understating its own
        # independence would stamp a caveat that is not true of the run.
        with pytest.raises(RecursionDepthJudgeNotIndependentError, match="stronger"):
            RecursionDepthManifest.model_validate(
                _manifest_payload(
                    reviewer={
                        "provider": "example-provider",
                        "model_id": "example-expert-001",
                        "capability": "expert",
                        "family": "example-family-b",
                    }
                )
            )

    def test_one_provider_serving_two_families_is_cross_family(self) -> None:
        # The case an aggregating connection puts everyone in, and the one a
        # provider-derived rule refused: both pairs are reached through the same
        # endpoint and neither trained the other.
        manifest = RecursionDepthManifest.model_validate(
            _manifest_payload(
                independence="cross_family",
                reviewer={
                    "provider": "example-provider",
                    "model_id": "example-expert-001",
                    "capability": "expert",
                    "family": "example-family-b",
                },
            )
        )

        assert manifest.executor.provider == manifest.reviewer.provider
        assert manifest.caveat() is None

    def test_two_connections_to_one_family_is_not_cross_family(self) -> None:
        # The mirror image, and the one a provider-derived rule waved through:
        # separate connections decorrelate nothing when the same organisation
        # trained both models.
        with pytest.raises(
            RecursionDepthJudgeNotIndependentError, match="shared family"
        ):
            RecursionDepthManifest.model_validate(
                _manifest_payload(
                    independence="cross_family",
                    reviewer={
                        "provider": "other-provider",
                        "model_id": "example-expert-001",
                        "capability": "expert",
                        "family": "example-family-a",
                    },
                )
            )

    def test_same_family_carries_its_caveat_and_cross_family_does_not(self) -> None:
        same = RecursionDepthManifest.model_validate(_manifest_payload())
        cross = RecursionDepthManifest.model_validate(
            _manifest_payload(
                independence="cross_family",
                reviewer={
                    "provider": "other-provider",
                    "model_id": "example-expert-001",
                    "capability": "expert",
                    "family": "example-family-b",
                },
            )
        )

        assert same.caveat() is not None
        assert cross.caveat() is None


class TestTheMatrixIsCoherent:
    """A cap nobody counted is a cap nobody records."""

    def test_a_depth_with_no_repetition_count_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no repetition count"):
            RecursionDepthManifest.model_validate(
                _manifest_payload(depths=[1, 2, 3], repetitions={1: 1, 2: 1})
            )

    def test_a_depth_outside_the_sweep_is_refused(self) -> None:
        with pytest.raises(ValueError, match="outside"):
            RecursionDepthManifest.model_validate(
                _manifest_payload(depths=[7], repetitions={7: 1})
            )

    def test_the_planned_cell_count_multiplies_out(self) -> None:
        manifest = RecursionDepthManifest.model_validate(_manifest_payload())

        assert manifest.planned_cells == (1 + 2) * 2

    def test_the_shipped_manifest_names_no_vendor(self) -> None:
        # The product privileges no vendor, and a committed manifest is
        # product surface.
        payload = yaml.safe_load(_COMMITTED_MANIFEST.read_text(encoding="utf-8"))

        for role in ("executor", "reviewer"):
            assert payload[role]["provider"].startswith("example-")
            assert payload[role]["model_id"].startswith("example-")


def _report(*, cells: tuple[CellRecord, ...]) -> RecursionDepthReport:
    """Build a minimal report for the emitter.

    Returns:
        The report.
    """
    return RecursionDepthReport(
        provenance=Provenance(
            generated_at=datetime(2026, 8, 21, tzinfo=UTC),
            git_commit=NotBlankStr("0123456789abcdef0123456789abcdef01234567"),
            git_dirty=False,
            manifest_sha256=NotBlankStr("sha256:" + "0" * 64),
            spec_id=NotBlankStr("sqlcsv"),
            requirement_count=42,
            executor=ModelPair(
                provider=NotBlankStr("example-provider"),
                model_id=NotBlankStr("example-capable-001"),
                capability="capable",
                family=NotBlankStr("example-family-a"),
            ),
            reviewer=ModelPair(
                provider=NotBlankStr("example-provider"),
                model_id=NotBlankStr("example-expert-001"),
                capability="expert",
                family=NotBlankStr("example-family-a"),
            ),
            independence=Independence.SAME_FAMILY,
        ),
        cells=cells,
        by_achieved_depth=(
            DepthPoint(
                depth=2,
                arm=Arm.GATED,
                delivered_claims=4,
                surviving_claims=3,
                cells=1,
                # Set, not defaulted: `_cost_series` skips a point booking no
                # runs, so a fixture leaving this at 0 renders no cost panel
                # and every assertion about that panel passes on an empty one.
                runs=1,
                cost=1.5,
                attempts=6,
            ),
            DepthPoint(
                depth=2,
                arm=Arm.UNGATED,
                delivered_claims=4,
                surviving_claims=1,
                cells=1,
                runs=1,
                cost=1.0,
                attempts=6,
            ),
        ),
        # The key shape `achieved_depth_histogram` actually emits, arm
        # included. Without the arm this fixture asserted a caption against a
        # string the sweep cannot produce, so a change to the real format
        # would have left the test green.
        achieved_depth_histogram={
            f"cap=2 {Arm.GATED.value} reached=2": 1,
            f"cap=2 {Arm.UNGATED.value} reached=2": 1,
        },
        caveats=(SIZING_CAVEAT, ORACLE_CAVEAT),
    )


def _measured_cell(arm: Arm) -> CellRecord:
    """Build one measured run.

    Returns:
        The cell.
    """
    return CellRecord(
        depth_cap=2,
        arm=arm,
        repetition=0,
        achieved_depth=1,
        units=(
            UnitRecord(
                unit_id=NotBlankStr("a"),
                title=NotBlankStr("build it"),
                kind=LEAF,
                depth=1,
                claimed=(NotBlankStr("R01"),),
                delivered=True,
                attempts=1,
                cost=0.5,
            ),
        ),
        merged_passing=(NotBlankStr("R01"),),
    )


class TestTheReportRefusesASilentGap:
    """A cell is measured or unavailable, never neither."""

    def test_a_cell_that_is_neither_is_refused(self) -> None:
        with pytest.raises(ValueError, match="either measured or unavailable"):
            CellRecord(depth_cap=1, arm=Arm.GATED, repetition=0)

    def test_a_cell_that_is_both_is_refused(self) -> None:
        with pytest.raises(ValueError, match="either measured or unavailable"):
            CellRecord(
                depth_cap=1,
                arm=Arm.GATED,
                repetition=0,
                achieved_depth=0,
                unavailable_reason="and also this",
            )

    def test_more_survivors_than_delivered_work_is_refused(self) -> None:
        with pytest.raises(ValueError, match="surviving claims"):
            DepthPoint(
                depth=1,
                arm=Arm.GATED,
                delivered_claims=1,
                surviving_claims=2,
                cells=1,
            )


class TestTheEmittedArtifacts:
    """Three files, and the caveats travel with the picture."""

    def test_all_three_are_written(self, tmp_path: Path) -> None:
        report = _report(cells=(_measured_cell(Arm.GATED), _measured_cell(Arm.UNGATED)))

        paths = write_report(report, tmp_path)

        assert [path.name for path in paths] == [
            "depth_curve.json",
            "depth_curve.md",
            "chart.svg",
        ]
        assert all(path.is_file() for path in paths)

    def test_the_chart_is_self_contained_and_theme_aware(self, tmp_path: Path) -> None:
        report = _report(cells=(_measured_cell(Arm.GATED),))

        _, _, svg_path = write_report(report, tmp_path)
        svg = svg_path.read_text(encoding="utf-8")

        assert svg.startswith("<svg")
        # No external reference of any kind: a chart that needs the network to
        # render is one nobody can read from a checkout.
        assert "http://www.w3.org/2000/svg" in svg
        assert "xlink" not in svg
        assert "prefers-color-scheme" in svg

    def test_the_caveats_are_drawn_into_the_chart(self, tmp_path: Path) -> None:
        # Not left in the prose beside it: the SVG is the one file that gets
        # pasted somewhere else, and a number separated from its caveats gets
        # over-read.
        report = _report(cells=(_measured_cell(Arm.GATED),))

        _, _, svg_path = write_report(report, tmp_path)
        svg = svg_path.read_text(encoding="utf-8")

        assert "PLANNER-DECLARED" in svg
        assert "held out" in svg
        assert f"cap=2 {Arm.GATED.value} reached=2" in svg

    def test_an_unavailable_cell_keeps_its_reason_in_the_report(
        self, tmp_path: Path
    ) -> None:
        # Never silently omitted: a curve of zeros and a curve nobody ran look
        # identical once the reasons are gone.
        unavailable = CellRecord(
            depth_cap=2,
            arm=Arm.UNGATED,
            repetition=0,
            unavailable_reason="the Docker daemon went away",
        )
        report = _report(cells=(_measured_cell(Arm.GATED), unavailable))

        _, md_path, _ = write_report(report, tmp_path)

        assert "the Docker daemon went away" in md_path.read_text(encoding="utf-8")
