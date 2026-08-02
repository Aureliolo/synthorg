"""Tests for the per-hook pre-push timing report.

The report's whole value is that it reads pre-commit's own measurement
rather than re-timing the hooks from outside, so the behaviour that
matters is parsing: a skipped hook must not read as 0.00s (that would
hide a hook nobody is paying for behind a number that looks measured),
and the two group runners' self-reported breakdowns must land under the
hook that produced them rather than under whichever hook ran next.
"""

import importlib.util
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]


class _TimingShape(Protocol):
    """The fields the tests read off one parsed hook timing."""

    hook_id: str
    seconds: float | None
    children: dict[str, float]
    runs: int

    @property
    def skipped(self) -> bool: ...


class _ReportModule(Protocol):
    """Subset of ``scripts/report_prepush_hook_timing.py`` under test."""

    parse_timings: Callable[[str], list[_TimingShape]]
    _render: Callable[[list[_TimingShape], float | None], str]
    _as_json: Callable[[list[_TimingShape], float | None, str], str]
    main: Callable[[Sequence[str] | None], int]


def _load() -> _ReportModule:
    script_path = _REPO_ROOT / "scripts" / "report_prepush_hook_timing.py"
    spec = importlib.util.spec_from_file_location(
        "report_prepush_hook_timing", script_path
    )
    if spec is None or spec.loader is None:
        msg = f"could not load module spec for {script_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_ReportModule, module)


_MODULE = _load()

# Verbatim shapes: pre-commit's own `- hook id:` / `- duration:` lines, the
# per-gate line run_prepush_python_gates.py prints, and the summary line
# run_prepush_hook_group.py prints. A drift in any of these silently empties
# the report, so the fixtures copy the real formats character for character.
_SAMPLE = """\
mypy type-check.....................................................Passed
- hook id: mypy
- duration: 12.5s

pytest unit tests...................................................Skipped
- hook id: pytest-unit

consolidated pre-push gates.........................................Passed
- hook id: consolidated-python-gates
- duration: 29.42s

  [ok  ]   0.4s  check_no_stubs
  [FAIL]  16.4s  check_persistence_boundary
consolidated pre-push gates: 65/65 reported in 29.1s across 12 job(s), 0 failed

web dashboard checks................................................Passed
- hook id: web-checks
- duration: 8.30s

web-checks: eslint 8.1s, knip 3.0s, circular 1.2s -- 8.2s wall-clock
"""


class TestParsing:
    """Reading pre-commit's verbose output back into per-hook numbers."""

    def test_hook_durations_are_read(self) -> None:
        timings = {t.hook_id: t.seconds for t in _MODULE.parse_timings(_SAMPLE)}
        assert timings["mypy"] == 12.5
        assert timings["consolidated-python-gates"] == 29.42
        assert timings["web-checks"] == 8.30

    def test_a_skipped_hook_is_skipped_not_zero(self) -> None:
        # 0.00s would read as "measured and free" in the table, which is the
        # opposite of "never ran".
        skipped = next(
            t for t in _MODULE.parse_timings(_SAMPLE) if t.hook_id == "pytest-unit"
        )
        assert skipped.seconds is None
        assert skipped.skipped

    def test_hooks_keep_the_order_pre_commit_ran_them(self) -> None:
        assert [t.hook_id for t in _MODULE.parse_timings(_SAMPLE)] == [
            "mypy",
            "pytest-unit",
            "consolidated-python-gates",
            "web-checks",
        ]

    def test_gate_lines_attach_to_the_hook_that_printed_them(self) -> None:
        parsed = {t.hook_id: t.children for t in _MODULE.parse_timings(_SAMPLE)}
        assert parsed["consolidated-python-gates"] == {
            "check_no_stubs": 0.4,
            "check_persistence_boundary": 16.4,
        }
        assert parsed["mypy"] == {}

    def test_a_failing_gate_still_reports_its_cost(self) -> None:
        # The FAIL marker is a verdict, not a reason to drop the measurement.
        parsed = next(
            t
            for t in _MODULE.parse_timings(_SAMPLE)
            if t.hook_id == "consolidated-python-gates"
        )
        assert parsed.children["check_persistence_boundary"] == 16.4

    def test_group_tool_lines_are_split_per_tool(self) -> None:
        parsed = next(
            t for t in _MODULE.parse_timings(_SAMPLE) if t.hook_id == "web-checks"
        )
        assert parsed.children == {"eslint": 8.1, "knip": 3.0, "circular": 1.2}

    def test_a_group_scope_note_does_not_invent_a_tool(self) -> None:
        text = (
            "- hook id: web-checks\n"
            "- duration: 9.0s\n"
            "web-checks: eslint 8.1s (whole scope: 3 paths too long to pass),"
            " knip 0.5s -- 8.6s wall-clock\n"
        )
        parsed = _MODULE.parse_timings(text)[0]
        assert parsed.children == {"eslint": 8.1, "knip": 0.5}

    def test_breakdown_before_any_hook_id_is_ignored(self) -> None:
        assert _MODULE.parse_timings("  [ok  ]   0.4s  check_no_stubs\n") == []

    def test_argv_chunked_reruns_are_summed_not_overwritten(self) -> None:
        # pre-commit splits a long filename list into argv-sized chunks and
        # invokes the hook once per chunk under one reported duration. Keeping
        # only the last chunk reports 3.4s for a hook that really cost 10.2s,
        # and hides that the whole-tree tools ran three times over.
        text = (
            "- hook id: web-checks\n"
            "- duration: 10.2s\n"
            "web-checks: knip 2.8s, circular 3.4s -- 3.4s wall-clock\n"
            "web-checks: knip 2.8s, circular 3.4s -- 3.4s wall-clock\n"
            "web-checks: knip 2.8s, circular 3.4s -- 3.4s wall-clock\n"
        )
        parsed = _MODULE.parse_timings(text)[0]
        assert parsed.runs == 3
        assert parsed.children["knip"] == pytest.approx(8.4)
        assert parsed.children["circular"] == pytest.approx(10.2)

    def test_a_single_invocation_is_not_flagged_as_chunked(self) -> None:
        parsed = next(
            t for t in _MODULE.parse_timings(_SAMPLE) if t.hook_id == "web-checks"
        )
        assert parsed.runs == 1
        assert "argv-chunked" not in _MODULE._render([parsed], None)

    def test_chunked_reruns_are_reported_in_the_table(self) -> None:
        text = (
            "- hook id: web-checks\n"
            "- duration: 10.2s\n"
            "web-checks: knip 2.8s -- 2.8s wall-clock\n"
            "web-checks: knip 2.8s -- 2.8s wall-clock\n"
        )
        rendered = _MODULE._render(_MODULE.parse_timings(text), None)
        assert "ran 2x, argv-chunked" in rendered

    def test_output_without_durations_parses_to_nothing(self) -> None:
        # A non-verbose run prints no durations; the caller must be able to
        # tell that apart from "every hook took zero seconds".
        assert _MODULE.parse_timings("Some hook...Passed\n") == []


class TestRendering:
    """The table and the JSON both have to survive a skipped hook."""

    def test_table_is_sorted_slowest_first(self) -> None:
        rendered = _MODULE._render(_MODULE.parse_timings(_SAMPLE), 60.0)
        rows = [line.split()[0] for line in rendered.splitlines() if line.strip()]
        assert rows.index("consolidated-python-gates") < rows.index("mypy")
        assert rows.index("mypy") < rows.index("web-checks")

    def test_skipped_hooks_are_listed_not_totalled(self) -> None:
        rendered = _MODULE._render(_MODULE.parse_timings(_SAMPLE), 60.0)
        assert "skipped (1): pytest-unit" in rendered
        assert "50.22" in rendered

    def test_json_round_trips_the_breakdown(self) -> None:
        payload = json.loads(
            _MODULE._as_json(_MODULE.parse_timings(_SAMPLE), 60.0, "pre-push")
        )
        assert payload["stage"] == "pre-push"
        assert payload["wall_clock_seconds"] == 60.0
        by_id = {hook["hook_id"]: hook for hook in payload["hooks"]}
        assert by_id["pytest-unit"]["seconds"] is None
        assert by_id["web-checks"]["children"]["eslint"] == 8.1


class TestFromLog:
    """Re-reading a captured run, so a measurement can be re-examined."""

    def test_parses_a_saved_log(self, tmp_path: Path) -> None:
        log = tmp_path / "prepush-last.log"
        log.write_text(_SAMPLE, encoding="utf-8")
        out = tmp_path / "timings.json"
        assert _MODULE.main(["--from-log", str(log), "--json", str(out)]) == 0
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert len(payload["hooks"]) == 4

    def test_a_missing_log_is_an_error_not_an_empty_report(
        self, tmp_path: Path
    ) -> None:
        assert _MODULE.main(["--from-log", str(tmp_path / "absent.log")]) == 2

    def test_a_log_with_no_timings_is_an_error(self, tmp_path: Path) -> None:
        # Exiting 0 here would report "no hooks cost anything" for a log that
        # simply was not captured under --verbose.
        log = tmp_path / "quiet.log"
        log.write_text("Some hook...Passed\n", encoding="utf-8")
        assert _MODULE.main(["--from-log", str(log)]) == 2
