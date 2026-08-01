"""Tests for the pytest-split durations generator.

The file this writes decides how the unit shards are partitioned, and a
silently-wrong one is invisible: CI still passes, it just goes back to
splitting by test count and one shard drifts over its budget. So the
properties under test are the ones that would fail quietly.
"""

import json
import textwrap
from pathlib import Path

import pytest
from scripts.generate_test_durations import main, merge_reports

pytestmark = pytest.mark.unit


def _report(*cases: tuple[str, str, str]) -> str:
    """Render a JUnit report from ``(classname, name, time)`` triples."""
    entries = "".join(
        f'<testcase classname="{cls}" name="{name}" time="{seconds}" />'
        for cls, name, seconds in cases
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<testsuites><testsuite name="pytest">{entries}</testsuite></testsuites>'
    )


def _tree(tmp_path: Path, *modules: str) -> Path:
    """Lay out empty test modules so classnames resolve against a tree."""
    for rel in modules:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    return tmp_path


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return path


class TestNodeIds:
    def test_a_class_test_becomes_a_double_colon_nodeid(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, "tests/unit/api/test_app.py")
        report = _write(
            tmp_path,
            "junit.xml",
            _report(("tests.unit.api.test_app.TestCreateApp", "test_builds", "1.5")),
        )
        merged = merge_reports([report], root)
        assert merged.durations == {
            "tests/unit/api/test_app.py::TestCreateApp::test_builds": 1.5
        }

    def test_a_module_level_test_has_no_class_segment(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, "tests/unit/core/test_thing.py")
        report = _write(
            tmp_path,
            "junit.xml",
            _report(("tests.unit.core.test_thing", "test_works", "0.5")),
        )
        merged = merge_reports([report], root)
        assert merged.durations == {"tests/unit/core/test_thing.py::test_works": 0.5}

    def test_a_nested_class_keeps_every_segment(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, "tests/unit/api/test_app.py")
        report = _write(
            tmp_path,
            "junit.xml",
            _report(("tests.unit.api.test_app.Outer.Inner", "test_deep", "0.5")),
        )
        merged = merge_reports([report], root)
        assert "tests/unit/api/test_app.py::Outer::Inner::test_deep" in merged.durations

    def test_a_parametrised_name_is_kept_verbatim(self, tmp_path: Path) -> None:
        # The bracket payload can contain dots, which must not be read as
        # module separators.
        root = _tree(tmp_path, "tests/unit/core/test_thing.py")
        report = _write(
            tmp_path,
            "junit.xml",
            _report(("tests.unit.core.test_thing", "test_x[1.5-a.b]", "0.5")),
        )
        merged = merge_reports([report], root)
        assert "tests/unit/core/test_thing.py::test_x[1.5-a.b]" in merged.durations


class TestMerging:
    def test_shards_are_unioned(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, "tests/unit/a/test_one.py", "tests/unit/b/test_two.py")
        first = _write(
            tmp_path, "j1.xml", _report(("tests.unit.a.test_one", "test_a", "1.0"))
        )
        second = _write(
            tmp_path, "j2.xml", _report(("tests.unit.b.test_two", "test_b", "2.0"))
        )
        merged = merge_reports([first, second], root)
        assert set(merged.durations) == {
            "tests/unit/a/test_one.py::test_a",
            "tests/unit/b/test_two.py::test_b",
        }

    def test_a_repeated_test_keeps_the_slower_reading(self, tmp_path: Path) -> None:
        # A re-run is the honest worst case; taking the faster one would
        # under-weight the test and re-imbalance the shard it lands in.
        root = _tree(tmp_path, "tests/unit/a/test_one.py")
        first = _write(
            tmp_path, "j1.xml", _report(("tests.unit.a.test_one", "test_a", "1.0"))
        )
        second = _write(
            tmp_path, "j2.xml", _report(("tests.unit.a.test_one", "test_a", "4.0"))
        )
        merged = merge_reports([first, second], root)
        assert merged.durations["tests/unit/a/test_one.py::test_a"] == 4.0

    def test_sub_threshold_tests_are_dropped(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, "tests/unit/a/test_one.py")
        report = _write(
            tmp_path,
            "junit.xml",
            _report(
                ("tests.unit.a.test_one", "test_fast", "0.001"),
                ("tests.unit.a.test_one", "test_slow", "1.0"),
            ),
        )
        merged = merge_reports([report], root)
        assert set(merged.durations) == {"tests/unit/a/test_one.py::test_slow"}

    def test_a_missing_module_is_tolerated_as_drift(self, tmp_path: Path) -> None:
        # A report predates a rename, or was recorded on a branch carrying
        # tests this tree lacks. A stale entry among a healthy suite is
        # drift, and the ratio is what tells it apart from a broken
        # resolver: one in sixty is well under the refusal threshold.
        root = _tree(tmp_path, "tests/unit/a/test_one.py")
        kept = [("tests.unit.a.test_one", f"test_{i}", "1.0") for i in range(60)]
        report = _write(
            tmp_path,
            "junit.xml",
            _report(*kept, ("tests.unit.gone.test_removed", "test_b", "1.0")),
        )
        merged = merge_reports([report], root)
        assert len(merged.durations) == len(kept)
        assert merged.unresolved == {"tests.unit.gone.test_removed"}
        assert merged.unresolved_cases == 1


class TestRefusals:
    def test_wholesale_unresolvable_reports_are_refused(self, tmp_path: Path) -> None:
        # Every classname missing means the path resolution broke, not that
        # the suite was deleted. Writing the near-empty file it would
        # produce sends the shards back to count-based partitioning with
        # nothing failing to say so.
        root = _tree(tmp_path, "tests/unit/a/test_one.py")
        report = _write(
            tmp_path,
            "junit.xml",
            _report(
                ("elsewhere.test_a", "test_a", "1.0"),
                ("elsewhere.test_b", "test_b", "1.0"),
                ("tests.unit.a.test_one", "test_kept", "1.0"),
            ),
        )
        with pytest.raises(ValueError, match="broken path resolution"):
            merge_reports([report], root)

    def test_an_unparseable_report_is_refused(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, "tests/unit/a/test_one.py")
        report = _write(tmp_path, "junit.xml", "not xml at all")
        with pytest.raises(ValueError, match="cannot read"):
            merge_reports([report], root)

    def test_reports_with_no_timed_tests_are_refused(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, "tests/unit/a/test_one.py")
        report = _write(
            tmp_path, "junit.xml", _report(("tests.unit.a.test_one", "test_a", "0.0"))
        )
        with pytest.raises(ValueError, match="empty"):
            merge_reports([report], root)


class TestCli:
    def test_writes_sorted_json(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, "tests/unit/a/test_one.py")
        report = _write(
            tmp_path,
            "junit.xml",
            _report(
                ("tests.unit.a.test_one", "test_z", "1.0"),
                ("tests.unit.a.test_one", "test_a", "2.0"),
            ),
        )
        out = tmp_path / "durations.json"
        code = main(
            [str(report), "--out", str(out), "--repo-root", str(root)],
        )
        assert code == 0
        written = json.loads(out.read_text(encoding="utf-8"))
        assert list(written) == [
            "tests/unit/a/test_one.py::test_a",
            "tests/unit/a/test_one.py::test_z",
        ]

    def test_a_refused_merge_writes_nothing(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, "tests/unit/a/test_one.py")
        report = _write(tmp_path, "junit.xml", "not xml at all")
        out = tmp_path / "durations.json"
        assert main([str(report), "--out", str(out), "--repo-root", str(root)]) == 1
        assert not out.exists()


def test_the_tracked_unit_durations_are_readable() -> None:
    """The committed file parses and carries real timings."""
    repo_root = Path(__file__).resolve().parents[3]
    durations = json.loads(
        (repo_root / ".test_durations.unit").read_text(encoding="utf-8")
    )
    assert durations, "the tracked durations file is empty"
    assert all(isinstance(value, float | int) for value in durations.values())
    assert all("::" in nodeid for nodeid in durations)
