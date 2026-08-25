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

from evals.errors import RecursionDepthSpendRepairEmptyError
from evals.harness.journal import open_journal
from evals.recursion_depth.emit import REPORT_JSON_NAME
from evals.recursion_depth.journal import SPEC, matrix_identity
from evals.recursion_depth.manifest import Arm, Independence, ModelPair
from evals.recursion_depth.models import (
    CEILING_CAVEAT,
    LEAF,
    METRIC_CAVEAT,
    ORACLE_CAVEAT,
    SIZING_CAVEAT,
    CellRecord,
    Provenance,
    UnitRecord,
)
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit

_LEAF_TASK = "a1b2c3d4-0000-4000-8000-000000000001"

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
    """Write one measured cell into a journal at *out_dir*."""
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
                    tokens=7,
                ),
            ),
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
        assert caveats == [METRIC_CAVEAT, SIZING_CAVEAT, ORACLE_CAVEAT]

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
