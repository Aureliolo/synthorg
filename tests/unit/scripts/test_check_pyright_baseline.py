"""Unit tests for ``scripts/check_pyright_baseline.py``.

The gate's value rests on three properties that are cheap to break and
invisible when broken:

* A report from a run that analysed nothing must not read as "every rule at
  zero". The workflow runs pyright under ``|| true``, so a broken venv or
  config still produces well-formed JSON, and comparing it naively passes a
  required check while checking nothing.
* Growth is per-rule, so a rule going up must be caught even when another
  rule went down by more.
* The first-seed exemption must not be reachable once the baseline is
  committed, or deleting the file locally resets the ratchet.
"""

import importlib.util
import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_pyright_baseline.py"


def _load_script_module() -> ModuleType:
    """Import the script as a module so private helpers are callable."""
    spec = importlib.util.spec_from_file_location(
        "_check_pyright_baseline",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load_script_module()

_PLENTY = 5000


def _report(
    rules: list[str], *, analysed: int = _PLENTY, severity: str = "error"
) -> dict[str, object]:
    """Build a pyright ``--outputjson`` payload naming the given rules."""
    return {
        "summary": {"filesAnalyzed": analysed},
        "generalDiagnostics": [{"severity": severity, "rule": rule} for rule in rules],
    }


def _write_report(path: Path, payload: Mapping[str, object]) -> Path:
    """Persist a report payload and return its path."""
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ── _load_counts ────────────────────────────────────────────────


def test_counts_group_by_rule(tmp_path: Path) -> None:
    """Errors are tallied per rule."""
    report = _write_report(tmp_path / "r.json", _report(["ruleA", "ruleA", "ruleB"]))
    assert _MODULE._load_counts(report) == Counter({"ruleA": 2, "ruleB": 1})


def test_non_error_severities_are_ignored(tmp_path: Path) -> None:
    """Warnings are not findings; only errors count against the baseline."""
    report = _write_report(tmp_path / "r.json", _report(["ruleA"], severity="warning"))
    assert _MODULE._load_counts(report) == Counter()


def test_missing_rule_falls_back_to_sentinel(tmp_path: Path) -> None:
    """A diagnostic with no rule still has to be counted somewhere."""
    payload = {
        "summary": {"filesAnalyzed": _PLENTY},
        "generalDiagnostics": [{"severity": "error"}],
    }
    report = _write_report(tmp_path / "r.json", payload)
    assert _MODULE._load_counts(report) == Counter({_MODULE._NO_RULE: 1})


@pytest.mark.parametrize("analysed", [0, 1, 99])
def test_report_from_a_tiny_analysis_is_refused(tmp_path: Path, analysed: int) -> None:
    """The vacuous-green guard: too few files means the run is broken."""
    report = _write_report(tmp_path / "r.json", _report([], analysed=analysed))
    with pytest.raises(_MODULE.ReportUnusableError):
        _MODULE._load_counts(report)


def test_report_without_summary_is_refused(tmp_path: Path) -> None:
    """A report that cannot prove what it analysed is not trusted."""
    report = _write_report(tmp_path / "r.json", {"generalDiagnostics": []})
    with pytest.raises(_MODULE.ReportUnusableError):
        _MODULE._load_counts(report)


# ── _violations ─────────────────────────────────────────────────


def test_rule_within_baseline_is_clean() -> None:
    """Fewer findings than allowed is the shrink direction."""
    assert _MODULE._violations(Counter({"ruleA": 2}), {"ruleA": 3}) == []


def test_rule_over_baseline_is_flagged() -> None:
    """A baselined rule going up is growth."""
    problems = _MODULE._violations(Counter({"ruleA": 4}), {"ruleA": 3})
    assert len(problems) == 1
    assert "ruleA" in problems[0]


def test_new_rule_is_flagged_as_a_new_category() -> None:
    """A rule absent from the baseline is a new class of error."""
    problems = _MODULE._violations(Counter({"ruleB": 1}), {"ruleA": 3})
    assert len(problems) == 1
    assert "NOT in the baseline" in problems[0]


def test_growth_is_per_rule_not_net_total() -> None:
    """The motivating case: one rule may not fund another's growth."""
    counts = Counter({"ruleA": 1, "ruleB": 4})
    baseline = {"ruleA": 3, "ruleB": 2}
    problems = _MODULE._violations(counts, baseline)
    assert sum(counts.values()) == sum(baseline.values())
    assert len(problems) == 1
    assert "ruleB" in problems[0]


# ── main ────────────────────────────────────────────────────────


@pytest.fixture
def isolated_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the module at a scratch baseline instead of the repo's."""
    path = tmp_path / "baseline.json"
    monkeypatch.setattr(_MODULE, "_BASELINE_PATH", path)
    return path


def test_missing_report_exits_two(tmp_path: Path) -> None:
    """A report path that does not exist is an invocation error."""
    assert _MODULE.main(["--report", str(tmp_path / "absent.json")]) == 2


def test_unusable_report_exits_two_and_does_not_pass(
    tmp_path: Path, isolated_baseline: Path
) -> None:
    """A zero-file analysis must not be reported as a clean run."""
    isolated_baseline.write_text(json.dumps({"ruleA": 3}), encoding="utf-8")
    report = _write_report(tmp_path / "r.json", _report([], analysed=0))
    assert _MODULE.main(["--report", str(report)]) == 2


def test_growth_fails_the_gate(tmp_path: Path, isolated_baseline: Path) -> None:
    """Enforcement mode rejects a report over the baseline."""
    isolated_baseline.write_text(json.dumps({"ruleA": 1}), encoding="utf-8")
    report = _write_report(tmp_path / "r.json", _report(["ruleA", "ruleA"]))
    assert _MODULE.main(["--report", str(report)]) == 1


def test_within_baseline_passes(tmp_path: Path, isolated_baseline: Path) -> None:
    """Enforcement mode accepts a report at or under the baseline."""
    isolated_baseline.write_text(json.dumps({"ruleA": 3}), encoding="utf-8")
    report = _write_report(tmp_path / "r.json", _report(["ruleA"]))
    assert _MODULE.main(["--report", str(report)]) == 0


# ── the seeding exemption ───────────────────────────────────────


def test_seeding_writes_without_a_growth_check(
    tmp_path: Path, isolated_baseline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With nothing committed there is no prior state to widen."""
    monkeypatch.setattr(_MODULE, "_baseline_is_committed", lambda: False)
    report = _write_report(tmp_path / "r.json", _report(["ruleA"] * 9))
    assert _MODULE.main(["--report", str(report), "--update-baseline"]) == 0
    assert json.loads(isolated_baseline.read_text(encoding="utf-8")) == {"ruleA": 9}


def test_committed_baseline_refuses_growth_even_if_file_is_deleted(
    tmp_path: Path, isolated_baseline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bypass this gate must not have.

    Deleting the working-tree file used to re-enter the seeding path, which
    rewrote the ratchet with no growth check at all. Committed history is the
    authority, so an absent file is still a committed baseline.
    """
    monkeypatch.setattr(_MODULE, "_baseline_is_committed", lambda: True)
    assert not isolated_baseline.exists()
    report = _write_report(tmp_path / "r.json", _report(["ruleA"] * 500))
    assert _MODULE.main(["--report", str(report), "--update-baseline"]) == 1
    assert not isolated_baseline.exists()


def test_growth_allowed_with_explicit_approval(
    tmp_path: Path, isolated_baseline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The documented escape hatch still works, and only with the env var."""
    monkeypatch.setattr(_MODULE, "_baseline_is_committed", lambda: True)
    monkeypatch.setenv(_MODULE._GROWTH_ENV, "1")
    isolated_baseline.write_text(json.dumps({"ruleA": 1}), encoding="utf-8")
    report = _write_report(tmp_path / "r.json", _report(["ruleA", "ruleA"]))
    assert _MODULE.main(["--report", str(report), "--update-baseline"]) == 0
    assert json.loads(isolated_baseline.read_text(encoding="utf-8")) == {"ruleA": 2}


def test_shrink_updates_the_baseline_downward(
    tmp_path: Path, isolated_baseline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ratcheting down needs no approval."""
    monkeypatch.setattr(_MODULE, "_baseline_is_committed", lambda: True)
    isolated_baseline.write_text(json.dumps({"ruleA": 5}), encoding="utf-8")
    report = _write_report(tmp_path / "r.json", _report(["ruleA"]))
    assert _MODULE.main(["--report", str(report), "--update-baseline"]) == 0
    assert json.loads(isolated_baseline.read_text(encoding="utf-8")) == {"ruleA": 1}


def test_real_baseline_is_committed() -> None:
    """The seeding exemption is closed for this repository."""
    assert _MODULE._baseline_is_committed() is True
