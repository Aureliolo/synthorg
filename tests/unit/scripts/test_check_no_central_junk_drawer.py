# mypy: disable-error-code="explicit-any"
"""Unit tests for ``scripts/check_no_central_junk_drawer.py``."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_no_central_junk_drawer.py"

_ENUMS_REL = "src/synthorg/core/enums.py"


def _load_gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_check_no_central_junk_drawer",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_GATE: Any = cast("Any", _load_gate())


# ── api/state.py AppState __slots__ counting ────────────────────


def test_count_state_slots_basic() -> None:
    src = "class AppState:\n    __slots__ = ('_a', '_b', '_c')\n"
    assert _GATE.count_state_slots(src) == 3


def test_count_state_slots_aggregates_across_classes() -> None:
    src = (
        "class AppStateServicesMixin:\n"
        "    __slots__ = ('_x', '_y')\n"
        "\n"
        "class AppState(AppStateServicesMixin):\n"
        "    __slots__ = ('_a',)\n"
    )
    assert _GATE.count_state_slots(src) == 3


def test_count_state_slots_no_slots_returns_zero() -> None:
    src = "class AppState:\n    pass\n"
    assert _GATE.count_state_slots(src) == 0


# ── Project scaffolding ─────────────────────────────────────────


def _make_project(tmp_path: Path) -> Path:
    """Materialise a synthetic project tree with the state.py junk drawer.

    ``core/enums.py`` is deliberately absent: it has been dissolved and
    must-not-exist enforcement expects it gone.
    """
    project = tmp_path
    (project / "src" / "synthorg" / "api").mkdir(parents=True)
    (project / "src" / "synthorg" / "api" / "state.py").write_text(
        "class AppState:\n    __slots__ = ('_a', '_b')\n", encoding="utf-8"
    )
    return project


def _write_baseline(project: Path, payload: dict[str, dict[str, int]]) -> Path:
    baseline = project / "scripts" / "_central_junk_drawer_baseline.json"
    baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline.write_text(
        json.dumps({"description": "", "counts": payload}), encoding="utf-8"
    )
    return baseline


# ── must-not-exist enforcement ──────────────────────────────────


def test_check_must_not_exist_passes_when_absent(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    assert _GATE.check_must_not_exist(project_root=project) == []


def test_check_must_not_exist_fails_when_recreated(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    enums = project / _ENUMS_REL
    enums.parent.mkdir(parents=True, exist_ok=True)
    enums.write_text("class A:\n    pass\n", encoding="utf-8")
    assert _GATE.check_must_not_exist(project_root=project) == [_ENUMS_REL]


def test_main_fails_when_enums_recreated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _make_project(tmp_path)
    baseline = _write_baseline(
        project, {"src/synthorg/api/state.py": {"state_slots": 2}}
    )
    enums = project / _ENUMS_REL
    enums.parent.mkdir(parents=True, exist_ok=True)
    enums.write_text("class A:\n    pass\n", encoding="utf-8")
    exit_code = _GATE.main(
        ["--project-root", str(project), "--baseline", str(baseline)]
    )
    assert exit_code == 1
    assert "dissolved junk-drawer modules" in capsys.readouterr().err


# ── state.py growth check ───────────────────────────────────────


def test_check_passes_when_counts_match_baseline(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    baseline = _write_baseline(
        project, {"src/synthorg/api/state.py": {"state_slots": 2}}
    )
    violations = _GATE.check(project_root=project, baseline_path=baseline)
    assert violations == []


def test_check_passes_when_counts_decrease(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    baseline = _write_baseline(
        project, {"src/synthorg/api/state.py": {"state_slots": 5}}
    )
    violations = _GATE.check(project_root=project, baseline_path=baseline)
    assert violations == []


def test_check_fails_when_state_slots_grow(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    baseline = _write_baseline(
        project,
        {"src/synthorg/api/state.py": {"state_slots": 1}},  # ← stale
    )
    violations = _GATE.check(project_root=project, baseline_path=baseline)
    assert len(violations) == 1
    assert "state.py" in violations[0].render()


def test_main_passes_when_clean(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    baseline = _write_baseline(
        project, {"src/synthorg/api/state.py": {"state_slots": 2}}
    )
    exit_code = _GATE.main(
        ["--project-root", str(project), "--baseline", str(baseline)]
    )
    assert exit_code == 0


# ── write_baseline ─────────────────────────────────────────────


def test_write_baseline_is_idempotent(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    baseline = project / "scripts" / "_central_junk_drawer_baseline.json"
    baseline.parent.mkdir(parents=True)
    _GATE.write_baseline(project_root=project, baseline_path=baseline)
    first = baseline.read_text(encoding="utf-8")
    _GATE.write_baseline(project_root=project, baseline_path=baseline)
    second = baseline.read_text(encoding="utf-8")
    assert first == second


def test_write_baseline_captures_current_counts(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    baseline = project / "scripts" / "_central_junk_drawer_baseline.json"
    baseline.parent.mkdir(parents=True)
    _GATE.write_baseline(project_root=project, baseline_path=baseline)
    payload = json.loads(baseline.read_text(encoding="utf-8"))
    assert payload["counts"]["src/synthorg/api/state.py"]["state_slots"] == 2
    assert _ENUMS_REL not in payload["counts"]


def test_main_update_baseline_writes_and_exits_zero(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    baseline = project / "scripts" / "_central_junk_drawer_baseline.json"
    baseline.parent.mkdir(parents=True)
    exit_code = _GATE.main(
        [
            "--project-root",
            str(project),
            "--baseline",
            str(baseline),
            "--update-baseline",
        ]
    )
    assert exit_code == 0
    payload = json.loads(baseline.read_text(encoding="utf-8"))
    assert set(payload["counts"]) == {"src/synthorg/api/state.py"}


def test_main_update_baseline_clears_stale_violations(tmp_path: Path) -> None:
    """--update-baseline rewrites a stale baseline and then exits 0.

    A baseline whose recorded count is below the current tree's would
    normally report a violation; --update-baseline overwrites it to the
    current counts so a fresh check passes.
    """
    project = _make_project(tmp_path)
    # Stale: baseline records 1 slot but the synthetic tree has 2.
    baseline = _write_baseline(
        project, {"src/synthorg/api/state.py": {"state_slots": 1}}
    )
    assert _GATE.check(project_root=project, baseline_path=baseline) != []
    exit_code = _GATE.main(
        [
            "--project-root",
            str(project),
            "--baseline",
            str(baseline),
            "--update-baseline",
        ]
    )
    assert exit_code == 0
    assert _GATE.check(project_root=project, baseline_path=baseline) == []
    payload = json.loads(baseline.read_text(encoding="utf-8"))
    assert payload["counts"]["src/synthorg/api/state.py"]["state_slots"] == 2
