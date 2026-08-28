# module-kind: tests
"""The matrix refuses a weakened judge, and the report refuses a silent gap."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from evals.errors import RecursionDepthJudgeNotIndependentError
from evals.recursion_depth.claims import RequirementId
from evals.recursion_depth.emit import derived_caveats, load_report, write_report
from evals.recursion_depth.manifest import (
    Arm,
    Independence,
    ModelPair,
    RecursionDepthManifest,
    load_manifest,
)
from evals.recursion_depth.models import (
    LEAF,
    MERGE,
    ORACLE_CAVEAT,
    RECURSION_DEPTH_SCHEMA_VERSION,
    SIZING_CAVEAT,
    CellRecord,
    DepthPoint,
    Provenance,
    RecursionDepthReport,
    SpendSource,
    SurvivalPoint,
    UnitRecord,
)
from evals.recursion_depth.spend_repair import SPEND_REPAIRED_CAVEAT
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.strategy_deps import (
    AgentSessionDecompositionConfig,
)

pytestmark = pytest.mark.unit

_RECURSION_DEPTH = Path(__file__).resolve().parents[3] / "evals" / "recursion_depth"
_COMMITTED_MANIFEST = _RECURSION_DEPTH / "manifest.yaml"
#: Every recording committed under `results/`, current and superseded. A tuple
#: rather than the generator `rglob` answers, which a second reader would find
#: already exhausted.
_COMMITTED_CURVES = tuple((_RECURSION_DEPTH / "results").rglob("depth_curve.json"))


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
        "planner_max_turns": 40,
        "unit_cost_ceiling": 2.0,
        "unit_token_ceiling": 600000,
        "max_sessions": 100,
        "projected_branching": 4,
        "expected_sessions_per_cell": {1: 20, 2: 60},
    }
    payload.update(overrides)
    return payload


class TestThePlannerAndTheUnitsAreBoundedSeparately:
    """One turn budget for both is a limit on one deciding the other.

    The shipped decomposition config refuses more than 50 turns and the unit
    loop takes its cap as a plain argument, so a shared field means a unit may
    never have more room than a planner is allowed, and a value chosen for a
    unit is refused for reasons that have nothing to do with units.
    """

    def test_the_planner_is_held_to_what_the_product_accepts(self) -> None:
        # Refused at LOAD. Accepted here, it would boot a host and then fail
        # every cell at dispatch, against a matrix nobody can record.
        with pytest.raises(ValidationError, match="planner_max_turns"):
            RecursionDepthManifest.model_validate(
                _manifest_payload(planner_max_turns=51)
            )

    def test_a_unit_may_exceed_the_planners_ceiling(self) -> None:
        manifest = RecursionDepthManifest.model_validate(
            _manifest_payload(unit_max_turns=120)
        )

        assert manifest.unit_max_turns == 120
        assert manifest.planner_max_turns == 40

    def test_the_shipped_planner_budget_is_one_the_product_accepts(self) -> None:
        # Against the real consumer rather than against the manifest's mirror
        # of its bound: a mirror that drifts is exactly the failure this pins,
        # and it can only be caught by asking the thing being mirrored.
        manifest = load_manifest(_COMMITTED_MANIFEST)

        config = AgentSessionDecompositionConfig(max_turns=manifest.planner_max_turns)

        assert config.max_turns == manifest.planner_max_turns


class TestTheJudgeMustBeIndependent:
    """The gate is the treatment, so a weakened judge biases toward the null."""

    def test_the_shipped_manifest_loads(self) -> None:
        manifest = load_manifest(_COMMITTED_MANIFEST)

        assert manifest.arms == (Arm.GATED,)
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

    @pytest.mark.parametrize(
        ("executor_family", "reviewer_family"),
        [(None, None), ("example-family-a", None), (None, "example-family-a")],
        ids=["neither-declared", "reviewer-silent", "executor-silent"],
    )
    def test_same_family_accepts_an_undeclared_family(
        self, executor_family: str | None, reviewer_family: str | None
    ) -> None:
        """An absent family contradicts nothing, and the caveat is the same.

        The asymmetry is deliberate and load-bearing: cross-family REFUSES an
        undeclared family, because the claim it makes rests on the two names
        being different and there is nothing to compare. Same-family claims
        only that the run is caveated, which is true whatever the names are.
        """
        executor = {
            "provider": "example-provider",
            "model_id": "example-capable-001",
            "capability": "capable",
        }
        reviewer = {
            "provider": "example-provider",
            "model_id": "example-expert-001",
            "capability": "expert",
        }
        if executor_family is not None:
            executor["family"] = executor_family
        if reviewer_family is not None:
            reviewer["family"] = reviewer_family

        manifest = RecursionDepthManifest.model_validate(
            _manifest_payload(executor=executor, reviewer=reviewer)
        )

        assert manifest.caveat() is not None

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

    def test_a_depth_with_no_expected_cost_is_refused(self) -> None:
        # A cap the refusal check cannot price is one the sweep would enter
        # without knowing whether it can finish it, which is the whole reason
        # the check exists.
        with pytest.raises(ValueError, match="no expected session cost"):
            RecursionDepthManifest.model_validate(
                _manifest_payload(expected_sessions_per_cell={1: 20})
            )

    def test_an_expected_cost_below_one_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no expected session cost"):
            RecursionDepthManifest.model_validate(
                _manifest_payload(expected_sessions_per_cell={1: 20, 2: 0})
            )

    def test_a_cost_for_an_unswept_cap_is_inert(self) -> None:
        # Load-bearing rather than lax. `narrow` rewrites `depths` and leaves
        # the mappings alone, so a validator refusing extra keys would refuse
        # every staged run, which is how a matrix this size is paid for.
        manifest = RecursionDepthManifest.model_validate(
            _manifest_payload(expected_sessions_per_cell={1: 20, 2: 60, 3: 200})
        )

        assert manifest.expected_sessions(2) == 60

    def test_the_shipped_matrix_is_the_replication_design(self) -> None:
        manifest = load_manifest(_COMMITTED_MANIFEST)

        assert manifest.depths == (1, 2, 3, 4)
        # Repetitions are concentrated where a cell is cheap. Every depth needs
        # a population or it reports a point rather than a range, and cap 1
        # earned its three: independently planned trees at that cap scored 37,
        # 37 and 23 of 42. The deep end is bracketed rather than sampled,
        # because one more cap-4 cell costs more than every cap-1 and cap-2
        # cell in the matrix put together.
        assert manifest.repetitions[1] == 3
        assert manifest.repetitions[2] == 3
        assert manifest.repetitions[3] == 2
        assert manifest.repetitions[4] == 2
        assert all(manifest.repetitions[depth] >= 2 for depth in manifest.depths)
        assert manifest.planned_cells == 10
        assert all(manifest.expected_sessions(depth) >= 1 for depth in manifest.depths)

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
                required=4,
                satisfied=3,
                # Set, not defaulted: `_cost_series` skips a point holding no
                # runs, so a fixture leaving this at 0 renders no cost panel
                # and every assertion about that panel passes on an empty one.
                cells=1,
                cost=1.5,
                attempts=6,
            ),
            DepthPoint(
                depth=2,
                arm=Arm.UNGATED,
                required=4,
                satisfied=1,
                cells=1,
                cost=1.0,
                attempts=6,
            ),
        ),
        # Set for the same reason the cost points are: the survival table and
        # the second chart panel render off these, so a fixture leaving them
        # empty asserts every claim about them against nothing. One arm
        # carries an EMPTY denominator, which is the case the two rendering
        # paths handle differently from zero.
        survival_by_achieved_depth=(
            SurvivalPoint(
                depth=2, arm=Arm.GATED, delivered_claims=4, surviving_claims=3, cells=1
            ),
            SurvivalPoint(
                depth=2,
                arm=Arm.UNGATED,
                delivered_claims=0,
                surviving_claims=0,
                cells=1,
            ),
        ),
        survival_by_depth_cap=(
            SurvivalPoint(
                depth=2, arm=Arm.GATED, delivered_claims=4, surviving_claims=3, cells=1
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
                claimed=(RequirementId("R01"),),
                delivered=True,
                attempts=1,
                cost=0.5,
            ),
        ),
        merged_passing=(RequirementId("R01"),),
    )


class TestTheRecordedJournalsStayReadable:
    """A rename must not strand the recordings that were paid for.

    ``missing_declared_paths`` was called ``undeclared_paths`` when every
    recording under ``results/`` was written, and those recordings are the
    project's only measurement of the loop. They stay re-scorable in place,
    so the old key is still accepted on the way in.
    """

    def test_the_old_key_still_populates_the_field(self) -> None:
        record = UnitRecord.model_validate(
            {
                "unit_id": "u1",
                "title": "Assemble: a thing",
                "kind": MERGE,
                "depth": 0,
                "undeclared_paths": ["src/a.py", "tests/test_a.py"],
            }
        )

        assert record.missing_declared_paths == ("src/a.py", "tests/test_a.py")

    def test_the_field_is_written_under_its_own_name(self) -> None:
        """A new recording carries the name that says which way it points."""
        record = UnitRecord(
            unit_id=NotBlankStr("u1"),
            title=NotBlankStr("Assemble: a thing"),
            kind=MERGE,
            depth=0,
            missing_declared_paths=("src/a.py",),
        )

        dumped = record.model_dump(mode="json")

        assert dumped["missing_declared_paths"] == ["src/a.py"]
        assert "undeclared_paths" not in dumped


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

    def test_a_version_one_artifact_is_refused_by_name(self) -> None:
        """A v1 report has no reading under v2, so it is refused as a VERSION.

        ``DepthPoint`` changed from a fraction of the leaves' own claims to a
        fraction of the specification, and ``extra="forbid"`` means the old
        field names cannot be read at all. Left at version 1, the file would
        pass the version field and fail on the shape, reporting an unknown
        field for what is a whole-artifact mismatch.
        """
        payload = _report(cells=(_measured_cell(Arm.GATED),)).model_dump(mode="json")
        payload["schema_version"] = 1

        with pytest.raises(ValidationError, match="schema version mismatch"):
            RecursionDepthReport.model_validate(payload)

    def test_every_committed_curve_carries_the_current_version(self) -> None:
        """Every shipped artifact is loadable by the code that ships with it.

        The files a version bump can leave behind are the recordings already
        committed, which nothing else re-validates. Discovered rather than
        listed, because a superseded recording keeps its directory beside the
        current one and a hand-written path would check whichever of them the
        last author remembered.
        """
        committed = sorted(_COMMITTED_CURVES)

        assert committed
        for path in committed:
            assert load_report(path).schema_version == RECURSION_DEPTH_SCHEMA_VERSION

    def test_satisfying_more_than_the_spec_asks_is_refused(self) -> None:
        # Now a check that the oracle and the provenance agree about WHICH
        # specification was run: both operands come from one requirement set,
        # so exceeding it means they have come apart.
        with pytest.raises(ValueError, match="satisfied against"):
            DepthPoint(
                depth=1,
                arm=Arm.GATED,
                required=1,
                satisfied=2,
                cells=1,
            )


class TestBothCurvesReachTheReader:
    """Two curves, reported side by side, and the divergence is the finding.

    A tree can satisfy most of the specification because the merging agent
    rebuilt everything itself, with no leaf work surviving at all, so a reader
    handed one number cannot tell those apart.
    """

    def test_the_markdown_carries_the_survival_table(self, tmp_path: Path) -> None:
        report = _report(cells=(_measured_cell(Arm.GATED),))

        _, md_path, _ = write_report(report, tmp_path)

        assert "survival" in md_path.read_text(encoding="utf-8").lower()

    def test_an_empty_denominator_reads_as_absent_rather_than_zero(
        self, tmp_path: Path
    ) -> None:
        """Nothing measured is not everything lost, and 0.0 says the second."""
        report = _report(cells=(_measured_cell(Arm.GATED),))

        _, md_path, _ = write_report(report, tmp_path)

        assert "n/a" in md_path.read_text(encoding="utf-8")

    def test_the_chart_carries_a_second_panel(self, tmp_path: Path) -> None:
        report = _report(cells=(_measured_cell(Arm.GATED),))

        _, _, svg_path = write_report(report, tmp_path)

        svg = svg_path.read_text(encoding="utf-8")
        assert "Fraction of the specification satisfied" in svg
        assert "Fraction of the delivered leaves' own claims" in svg

    def test_the_json_carries_both_series(self, tmp_path: Path) -> None:
        report = _report(cells=(_measured_cell(Arm.GATED),))

        json_path, _, _ = write_report(report, tmp_path)
        reloaded = load_report(json_path)

        assert len(reloaded.survival_by_achieved_depth) == 2
        assert len(reloaded.survival_by_depth_cap) == 1

    def test_an_absent_point_is_left_out_of_the_chart_rather_than_drawn_low(
        self, tmp_path: Path
    ) -> None:
        """Drawing it at zero would read as everything having been lost, so
        the panel says how many buckets it left out instead."""
        report = _report(cells=(_measured_cell(Arm.GATED),))

        _, _, svg_path = write_report(report, tmp_path)

        assert "which is not a rate of zero" in svg_path.read_text(encoding="utf-8")


class TestTheCaveatsARecordingImplies:
    """Derived from the cells, so a re-score cannot forget one."""

    def test_a_clean_recording_implies_none(self) -> None:
        cells = (_measured_cell(Arm.GATED),)

        assert derived_caveats(cells, spend_source=SpendSource.JOURNALLED) == []

    def test_an_unattributed_bucket_is_named(self) -> None:
        cells = (
            _measured_cell(Arm.GATED).model_copy(
                update={"units": (), "merged_passing": ()}
            ),
        )

        caveats = derived_caveats(cells, spend_source=SpendSource.JOURNALLED)

        assert any("no survival point" in line for line in caveats)

    def test_a_repaired_column_is_declared(self) -> None:
        cells = (_measured_cell(Arm.GATED),)

        caveats = derived_caveats(cells, spend_source=SpendSource.REPAIRED)

        assert SPEND_REPAIRED_CAVEAT in caveats

    def test_two_conditions_produce_two_lines_rather_than_clobbering(self) -> None:
        """They are separate facts and a reader needs both."""
        cells = (
            _measured_cell(Arm.GATED).model_copy(
                update={"units": (), "merged_passing": ()}
            ),
        )

        caveats = derived_caveats(cells, spend_source=SpendSource.REPAIRED)

        assert len(caveats) == 2
        assert SPEND_REPAIRED_CAVEAT in caveats


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


_EXECUTOR_PAIR = ModelPair(
    provider=NotBlankStr("example-provider"),
    model_id=NotBlankStr("example-capable-001"),
    capability="capable",
    family=NotBlankStr("example-family-a"),
)

_REVIEWER_PAIR = ModelPair(
    provider=NotBlankStr("example-provider"),
    model_id=NotBlankStr("example-expert-001"),
    capability="expert",
    family=NotBlankStr("example-family-b"),
)


def _cell_with_a_merge(arm: Arm, *, reviewer: ModelPair | None) -> CellRecord:
    """One measured run holding a leaf and the assembly above it.

    Args:
        arm: Which arm the run belongs to.
        reviewer: The pair that judged the merge, ``None`` in the ungated arm
            where nobody does.

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
                unit_id=NotBlankStr(f"{arm.value}-leaf"),
                title=NotBlankStr("build it"),
                kind=LEAF,
                depth=1,
                claimed=(RequirementId("R01"),),
                delivered=True,
                attempts=1,
                tokens=100,
                cost=0.5,
                executor=_EXECUTOR_PAIR,
            ),
            UnitRecord(
                unit_id=NotBlankStr(f"{arm.value}-merge"),
                title=NotBlankStr("Assemble: the whole thing"),
                kind=MERGE,
                depth=0,
                delivered=True,
                attempts=4,
                turns=9,
                tokens=900,
                cost=1.25,
                executor=_EXECUTOR_PAIR,
                reviewer=reviewer,
                verdict=NotBlankStr("approve") if reviewer is not None else None,
                amendments=2,
            ),
        ),
        merged_passing=(RequirementId("R01"),),
    )


class TestTheReportNamesBothPartiesPerMerge:
    """The gate is the treatment, so who judged is part of the result.

    A reviewer that silently came up on the executor's own binding biases the
    gated arm toward the null while every sweep-level field still reads
    correctly, so the pairing has to be legible at the grain it happened at.
    """

    def _markdown(self, tmp_path: Path) -> str:
        """Emit a two-arm report and return its Markdown.

        Returns:
            The rendered report.
        """
        report = _report(
            cells=(
                _cell_with_a_merge(Arm.GATED, reviewer=_REVIEWER_PAIR),
                _cell_with_a_merge(Arm.UNGATED, reviewer=None),
            )
        )
        _, md_path, _ = write_report(report, tmp_path)
        return md_path.read_text(encoding="utf-8")

    def test_the_pairing_table_names_both_families(self, tmp_path: Path) -> None:
        rendered = self._markdown(tmp_path)

        assert "## Who judged whom" in rendered
        assert "example-provider/example-capable-001 (example-family-a)" in rendered
        assert "example-provider/example-expert-001 (example-family-b)" in rendered

    def test_an_unjudged_merge_says_so_rather_than_rendering_blank(
        self, tmp_path: Path
    ) -> None:
        # The ungated arm's definition is that nobody judged, and a blank cell
        # reads as a recording that lost the reviewer rather than as the arm
        # working exactly as designed.
        rendered = self._markdown(tmp_path)

        assert "| none |" in rendered

    def test_every_merge_gets_its_own_row(self, tmp_path: Path) -> None:
        rendered = self._markdown(tmp_path)

        assert "## Every merge" in rendered
        assert rendered.count("| Assemble: the whole thing |") == 2
        assert "d2-gated-r0" in rendered
        assert "d2-ungated-r0" in rendered

    def test_a_title_carrying_table_syntax_stays_in_one_row(
        self, tmp_path: Path
    ) -> None:
        """One row per merge, whatever the agent called its unit.

        A unit title is agent-authored, so a ``|`` splits one merge across
        extra cells and a newline splits it across extra ROWS, which is
        exactly the claim this table makes. Neither shows up as an error: the
        file simply renders a table that says something else.
        """
        awkward = _cell_with_a_merge(Arm.GATED, reviewer=_REVIEWER_PAIR)
        merge = awkward.units[1].model_copy(
            update={"title": NotBlankStr("Assemble: a|b\nand c")}
        )
        report = _report(
            cells=(awkward.model_copy(update={"units": (awkward.units[0], merge)}),)
        )

        _, md_path, _ = write_report(report, tmp_path)
        rendered = md_path.read_text(encoding="utf-8")

        assert "| Assemble: a\\|b and c |" in rendered
        merge_rows = [
            line
            for line in rendered.splitlines()
            if line.startswith("| d2-gated-r0 |") and "Assemble:" in line
        ]
        assert len(merge_rows) == 1

    def test_each_arm_reports_what_merging_cost_it(self, tmp_path: Path) -> None:
        # Repair in the gated arm alone would let it win by spending more
        # rather than by catching anything, so the equal-budget claim is read
        # off this table or nowhere.
        rendered = self._markdown(tmp_path)

        assert "| gated | 1 | 4 | 900 | 1.2500 | 0 | 2 |" in rendered
        assert "| ungated | 1 | 4 | 900 | 1.2500 | 0 | 2 |" in rendered
