# module-kind: tests
"""Re-emitting a finished recording's report without paying for it again.

A re-score is the only way a scoring or rendering defect found after a
multi-hour run is corrected at all, so the two things worth pinning are what it
refuses (a repair that placed nothing, which would ship a caveat claiming a
repair that did not happen) and which caveats survive it, since the run-state
ones exist nowhere but the previous report.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from scripts.record_recursion_depth import _previous_caveats, _rescore

from evals.errors import (
    RecursionDepthSpendAlreadyAdoptedError,
    RecursionDepthSpendRepairEmptyError,
)
from evals.harness.journal import open_journal
from evals.recursion_depth.claims import RequirementId
from evals.recursion_depth.emit import REPORT_JSON_NAME
from evals.recursion_depth.journal import (
    JOURNAL_NAME,
    RAW_JOURNAL_NAME,
    SPEC,
    adopt_repaired_spend,
    matrix_identity,
    read_recorded_cells,
)
from evals.recursion_depth.manifest import Arm, Independence, ModelPair
from evals.recursion_depth.models import (
    CEILING_CAVEAT,
    HEADLINE_CAVEAT,
    LEAF,
    METRIC_CAVEAT,
    ORACLE_CAVEAT,
    SIZING_CAVEAT,
    CellRecord,
    Provenance,
    SpendSource,
    UnitRecord,
)
from evals.recursion_depth.spend_repair import SPEND_REPAIRED_CAVEAT
from synthorg.core.types import NotBlankStr
from tests._shared import sid

pytestmark = pytest.mark.unit

_LEAF_TASK = sid("leaf")

_EXECUTOR = ModelPair(
    provider=NotBlankStr("example-provider"),
    model_id=NotBlankStr("example-capable-001"),
    capability="capable",
    family=NotBlankStr("example-family-a"),
)
_REVIEWER = ModelPair(
    provider=NotBlankStr("example-provider"),
    model_id=NotBlankStr("example-expert-001"),
    capability="expert",
    family=NotBlankStr("example-family-b"),
)


def _provenance() -> Provenance:
    """The recording's own provenance.

    Returns:
        The block a re-score reads back off the journal header.
    """
    return Provenance(
        git_commit=NotBlankStr("0" * 40),
        git_dirty=False,
        generated_at=datetime(2026, 8, 25, tzinfo=UTC),
        manifest_sha256=NotBlankStr("sha256:" + "b" * 64),
        spec_id=NotBlankStr("sqlcsv"),
        requirement_count=42,
        executor=_EXECUTOR,
        reviewer=_REVIEWER,
        independence=Independence.CROSS_FAMILY,
    )


def _recorded(out_dir: Path) -> None:
    """Write one measured cell into a journal at *out_dir*.

    Its leaf delivers and claims, so the survival curve has a point and this
    file's cases are about caveat WORDING rather than about a bucket with an
    empty denominator, which derives a caveat of its own.
    """
    journal, _ = open_journal(
        out_dir, SPEC, identity=matrix_identity(_provenance()), resume=False
    )
    journal.record(
        CellRecord(
            depth_cap=1,
            arm=Arm.GATED,
            repetition=0,
            achieved_depth=1,
            units=(
                UnitRecord(
                    unit_id=NotBlankStr(_LEAF_TASK),
                    title=NotBlankStr("a"),
                    kind=LEAF,
                    depth=0,
                    claimed=(RequirementId("R01"),),
                    delivered=True,
                    tokens=7,
                ),
            ),
            merged_passing=(RequirementId("R01"),),
        )
    )
    journal.close()


class TestWhatARescoreRefuses:
    """A caveat is a provenance claim, so an empty repair is not reported."""

    def test_a_repair_that_placed_nothing_raises(self, tmp_path: Path) -> None:
        _recorded(tmp_path)
        log = tmp_path / "run.log"
        log.write_text("nothing this parser recognises\n", encoding="utf-8")

        with pytest.raises(RecursionDepthSpendRepairEmptyError):
            _rescore(tmp_path, repair_from=log)

    def test_another_recordings_log_raises(self, tmp_path: Path) -> None:
        """Parses fully, names nothing here, and would still stamp REPAIRED."""
        _recorded(tmp_path)
        other = sid("elsewhere")
        log = tmp_path / "run.log"
        log.write_text(
            f"cost.recorded call_category=productive task_id={other} "
            f"input_tokens=40 output_tokens=2\n"
            f"evals.harness.record_journalled cell=d9-ungated-r3/{other}\n",
            encoding="utf-8",
        )

        with pytest.raises(RecursionDepthSpendRepairEmptyError):
            _rescore(tmp_path, repair_from=log)

    def test_another_recordings_log_leaves_the_column_journalled(
        self, tmp_path: Path
    ) -> None:
        _recorded(tmp_path)
        other = sid("elsewhere")
        log = tmp_path / "run.log"
        log.write_text(
            f"cost.recorded call_category=productive task_id={other} "
            f"input_tokens=40 output_tokens=2\n"
            f"evals.harness.record_journalled cell=d9-ungated-r3/{other}\n",
            encoding="utf-8",
        )

        with pytest.raises(RecursionDepthSpendRepairEmptyError):
            _rescore(tmp_path, repair_from=log)

        provenance, cells = read_recorded_cells(tmp_path)
        assert provenance.spend_source is SpendSource.JOURNALLED
        assert [unit.tokens for cell in cells for unit in cell.units] == [7]


class TestWhichCaveatsSurvive:
    """Rebuilt by default, carried only by declaration."""

    def test_the_standing_caveats_are_rebuilt_at_this_wording(
        self, tmp_path: Path
    ) -> None:
        # Carried forward instead, an old recording would keep whatever
        # sentence was current when it ran, for ever.
        _recorded(tmp_path)
        (tmp_path / REPORT_JSON_NAME).write_text(
            json.dumps({"caveats": ["a sentence this release no longer writes"]}),
            encoding="utf-8",
        )

        _rescore(tmp_path, repair_from=None)

        caveats = json.loads((tmp_path / REPORT_JSON_NAME).read_text(encoding="utf-8"))[
            "caveats"
        ]
        assert caveats == [
            METRIC_CAVEAT,
            HEADLINE_CAVEAT,
            SIZING_CAVEAT,
            ORACLE_CAVEAT,
        ]

    def test_a_run_state_caveat_is_carried(self, tmp_path: Path) -> None:
        # The journal records cells, not why the sweep stopped, so this one
        # exists in the previous report and nowhere else.
        _recorded(tmp_path)
        (tmp_path / REPORT_JSON_NAME).write_text(
            json.dumps({"caveats": [CEILING_CAVEAT]}), encoding="utf-8"
        )

        _rescore(tmp_path, repair_from=None)

        caveats = json.loads((tmp_path / REPORT_JSON_NAME).read_text(encoding="utf-8"))[
            "caveats"
        ]
        assert CEILING_CAVEAT in caveats


def _repairable(out_dir: Path) -> Path:
    """Write a recording whose journalled spend a log can repair.

    Returns:
        The log to repair from.
    """
    _recorded(out_dir)
    log = out_dir / "run.log"
    log.write_text(
        f"cost.recorded call_category=productive task_id={_LEAF_TASK} "
        f"input_tokens=40 output_tokens=2\n"
        f"evals.harness.record_journalled cell=d1-gated-r0/{_LEAF_TASK}\n",
        encoding="utf-8",
    )
    return log


class TestARepairBecomesTheLedger:
    """A repair applied only at scoring time is not reproducible by anyone.

    The recorder log it reads is not a committed thing, so the next re-score of
    the same recording reads the journal, finds the raw figures, and publishes
    a column the report's own caveat calls scrambled.
    """

    def test_the_repaired_column_is_written_back_to_the_journal(
        self, tmp_path: Path
    ) -> None:
        log = _repairable(tmp_path)

        _rescore(tmp_path, repair_from=log)

        _, cells = read_recorded_cells(tmp_path)
        assert [unit.tokens for cell in cells for unit in cell.units] == [42]

    def test_the_journalled_figures_are_kept_beside_it(self, tmp_path: Path) -> None:
        """Real spend, so the ledger it replaced is moved rather than dropped."""
        log = _repairable(tmp_path)

        _rescore(tmp_path, repair_from=log)

        assert (tmp_path / RAW_JOURNAL_NAME).exists()

    def test_a_later_rescore_keeps_the_repaired_column(self, tmp_path: Path) -> None:
        """The whole point: nobody needs the log a second time."""
        log = _repairable(tmp_path)
        _rescore(tmp_path, repair_from=log)

        _rescore(tmp_path, repair_from=None)

        payload = json.loads((tmp_path / REPORT_JSON_NAME).read_text(encoding="utf-8"))
        assert payload["total_tokens"] == 42

    def test_the_repair_is_claimed_by_the_data_rather_than_the_flag(
        self, tmp_path: Path
    ) -> None:
        log = _repairable(tmp_path)
        _rescore(tmp_path, repair_from=log)

        _rescore(tmp_path, repair_from=None)

        payload = json.loads((tmp_path / REPORT_JSON_NAME).read_text(encoding="utf-8"))
        assert payload["provenance"]["spend_source"] == SpendSource.REPAIRED.value
        assert SPEND_REPAIRED_CAVEAT in payload["caveats"]

    def test_an_unrepaired_recording_claims_nothing(self, tmp_path: Path) -> None:
        _recorded(tmp_path)

        _rescore(tmp_path, repair_from=None)

        payload = json.loads((tmp_path / REPORT_JSON_NAME).read_text(encoding="utf-8"))
        assert payload["provenance"]["spend_source"] == SpendSource.JOURNALLED.value
        assert SPEND_REPAIRED_CAVEAT not in payload["caveats"]


class TestASecondRepairIsRefused:
    """The raw ledger is the one thing a repair cannot re-derive.

    A second repair reads what the first one WROTE, so adopting it again moves
    repaired figures on top of the journal kept precisely so a reader could
    check the claim. Trying the repair again after fixing an incomplete log is
    an ordinary operator move, and the log produces repaired figures by
    construction, so the original is gone for good.
    """

    def test_repairing_twice_raises(self, tmp_path: Path) -> None:
        log = _repairable(tmp_path)
        _rescore(tmp_path, repair_from=log)

        with pytest.raises(RecursionDepthSpendAlreadyAdoptedError):
            _rescore(tmp_path, repair_from=log)

    def test_the_original_figures_survive_the_refusal(self, tmp_path: Path) -> None:
        log = _repairable(tmp_path)
        _rescore(tmp_path, repair_from=log)
        before = (tmp_path / RAW_JOURNAL_NAME).read_text(encoding="utf-8")

        with pytest.raises(RecursionDepthSpendAlreadyAdoptedError):
            _rescore(tmp_path, repair_from=log)

        assert (tmp_path / RAW_JOURNAL_NAME).read_text(encoding="utf-8") == before

    def test_the_refusal_names_the_file_to_move(self, tmp_path: Path) -> None:
        log = _repairable(tmp_path)
        _rescore(tmp_path, repair_from=log)

        with pytest.raises(RecursionDepthSpendAlreadyAdoptedError) as caught:
            _rescore(tmp_path, repair_from=log)

        assert RAW_JOURNAL_NAME in str(caught.value)


class TestTheLedgerIsNeverAbsent:
    """No instant of the adoption leaves the directory unreadable.

    Writing the replacement over a journal that had already been renamed away
    left a window where a crash meant no ledger at all, which reads exactly
    like a recording that was never taken.
    """

    def test_a_failed_adoption_leaves_the_original_in_place(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _recorded(tmp_path)
        provenance, cells = read_recorded_cells(tmp_path)
        before = (tmp_path / JOURNAL_NAME).read_text(encoding="utf-8")

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise OSError(28, "No space left on device")

        monkeypatch.setattr("evals.recursion_depth.journal.copy2", _boom)
        with pytest.raises(OSError, match="No space left"):
            adopt_repaired_spend(tmp_path, provenance=provenance, cells=cells)

        assert (tmp_path / JOURNAL_NAME).read_text(encoding="utf-8") == before

    def test_a_failed_adoption_leaves_no_staging_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _recorded(tmp_path)
        provenance, cells = read_recorded_cells(tmp_path)

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise OSError(28, "No space left on device")

        monkeypatch.setattr("evals.recursion_depth.journal.copy2", _boom)
        with pytest.raises(OSError, match="No space left"):
            adopt_repaired_spend(tmp_path, provenance=provenance, cells=cells)

        assert not [p for p in tmp_path.iterdir() if p.name.startswith(".adopt-")]

    def test_a_swap_that_fails_after_the_copy_leaves_no_sentinel(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The raw journal is the guard's sentinel, so a half-done swap is a trap."""
        _recorded(tmp_path)
        provenance, cells = read_recorded_cells(tmp_path)

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(Path, "replace", _boom)
        with pytest.raises(OSError, match="No space left"):
            adopt_repaired_spend(tmp_path, provenance=provenance, cells=cells)
        monkeypatch.undo()

        assert not (tmp_path / RAW_JOURNAL_NAME).exists()

    def test_a_repair_can_be_retried_after_a_failed_swap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = _repairable(tmp_path)

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(Path, "replace", _boom)
        with pytest.raises(OSError, match="No space left"):
            _rescore(tmp_path, repair_from=log)
        monkeypatch.undo()

        assert _rescore(tmp_path, repair_from=log) == 0
        _, cells = read_recorded_cells(tmp_path)
        assert [unit.tokens for cell in cells for unit in cell.units] == [42]

    def test_a_write_failure_mid_loop_still_frees_the_staging_handle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The staging journal is never closed on this path when the loop
        fails, so a leaked handle here would block ``rmtree`` on Windows and
        leave the ``.adopt-*`` directory behind permanently."""
        _recorded(tmp_path)
        provenance, cells = read_recorded_cells(tmp_path)

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise OSError(28, "No space left on device")

        monkeypatch.setattr("evals.harness.journal.RunJournal.record", _boom)
        with pytest.raises(OSError, match="No space left"):
            adopt_repaired_spend(tmp_path, provenance=provenance, cells=cells)

        assert not [p for p in tmp_path.iterdir() if p.name.startswith(".adopt-")]


class TestReadingThePreviousReport:
    """Absent is ordinary; present and unreadable is not."""

    def test_no_previous_report_is_silent(self, tmp_path: Path) -> None:
        assert _previous_caveats(tmp_path) == ()

    @pytest.mark.parametrize(
        "written", ["{not json", json.dumps(["a list, not an object"])]
    )
    def test_an_unreadable_one_is_reported_rather_than_raising(
        self, tmp_path: Path, written: str
    ) -> None:
        # A truncated or foreign file used to reach `.get` on a list and raise
        # AttributeError out of a function documented never to fail.
        (tmp_path / REPORT_JSON_NAME).write_text(written, encoding="utf-8")

        assert _previous_caveats(tmp_path) == ()
