"""Unit tests for the architecture feedback loop.

Covers ``scripts/_architecture_lib.py`` (the metric primitives) and
``scripts/check_architecture_drift.py`` (the drift gate). The fan-in
metric depends on a live ``grimp`` graph of the installed package, so
the gate tests monkeypatch the metric functions with synthetic results
to exercise the regression logic in isolation (no graph build).
"""

import ast
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

from tests._shared import JsonDict

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_LIB_PATH = _REPO_ROOT / "scripts" / "_architecture_lib.py"
_GATE_PATH = _REPO_ROOT / "scripts" / "check_architecture_drift.py"


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_LIB: Any = cast("Any", _load("_architecture_lib", _LIB_PATH))  # type: ignore[explicit-any]  # dynamically loaded gate module; attrs resolved by name
_GATE: Any = cast("Any", _load("_check_architecture_drift", _GATE_PATH))  # type: ignore[explicit-any]  # dynamically loaded gate module; attrs resolved by name


def _parse_class(source: str) -> ast.ClassDef:
    return next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef))


# ── LCOM4 ───────────────────────────────────────────────────────


def test_lcom4_single_method_is_cohesive() -> None:
    cls = _parse_class("class C:\n    def a(self):\n        self.x = 1\n")
    assert _LIB._lcom4(cls) == 1


def test_lcom4_shared_attribute_is_one_component() -> None:
    cls = _parse_class(
        "class C:\n"
        "    def a(self):\n        self.x = 1\n"
        "    def b(self):\n        return self.x\n"
    )
    assert _LIB._lcom4(cls) == 1


def test_lcom4_method_call_links_components() -> None:
    cls = _parse_class(
        "class C:\n"
        "    def a(self):\n        return self.b()\n"
        "    def b(self):\n        return 1\n"
    )
    assert _LIB._lcom4(cls) == 1


def test_lcom4_disjoint_state_is_two_components() -> None:
    cls = _parse_class(
        "class C:\n"
        "    def a(self):\n        self.x = 1\n"
        "    def b(self):\n        return self.x\n"
        "    def c(self):\n        self.y = 2\n"
        "    def d(self):\n        return self.y\n"
    )
    assert _LIB._lcom4(cls) == 2


# ── budget pressure ─────────────────────────────────────────────


def _write(project: Path, rel: str, *, lines: int, header: str | None = None) -> None:
    path = project / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"x{i} = {i}" for i in range(lines))
    text = (f"{header}\n{body}" if header else body) + "\n"
    path.write_text(text, encoding="utf-8")


def test_budget_pressure_flags_file_near_cap(tmp_path: Path) -> None:
    # code tier cap is 500; 450 LOC is 0.9 -> flagged.
    _write(tmp_path, "src/synthorg/big.py", lines=450)
    pressure = _LIB.compute_budget_pressure(tmp_path)
    assert "src/synthorg/big.py" in pressure
    assert pressure["src/synthorg/big.py"]["tier"] == "code"


def test_budget_pressure_ignores_small_file(tmp_path: Path) -> None:
    _write(tmp_path, "src/synthorg/small.py", lines=100)
    assert _LIB.compute_budget_pressure(tmp_path) == {}


def test_budget_pressure_skips_declarative(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/synthorg/data.py",
        lines=900,
        header="# module-kind: declarative",
    )
    assert _LIB.compute_budget_pressure(tmp_path) == {}


# ── drift gate ──────────────────────────────────────────────────

_EMPTY_BASELINE: JsonDict = {"fan_in": {}, "budget_pressure": {}, "lcom": {}}


def _patch_metrics(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fan_in: dict[str, int] | None = None,
    budget: JsonDict | None = None,
    lcom: JsonDict | None = None,
) -> None:
    monkeypatch.setattr(_GATE._LIB, "compute_fan_in", lambda **_: fan_in or {})
    monkeypatch.setattr(_GATE._LIB, "compute_budget_pressure", lambda _: budget or {})
    monkeypatch.setattr(_GATE._LIB, "compute_lcom", lambda _: lcom or {})


def test_drift_clean_when_live_matches_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_metrics(monkeypatch, fan_in={"synthorg.core.types": 100})
    baseline = {
        "fan_in": {"synthorg.core.types": 100},
        "budget_pressure": {},
        "lcom": {},
    }
    assert _GATE.check(project_root=tmp_path, baseline=baseline) == []


def test_drift_fails_on_new_fan_in_hub(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A module imported by 55 others that the baseline never recorded:
    # the synthetic +50 fan-in acceptance case.
    _patch_metrics(monkeypatch, fan_in={"synthorg.workers.dispatcher": 55})
    violations = _GATE.check(project_root=tmp_path, baseline=_EMPTY_BASELINE)
    assert any("fan-in" in v and "synthorg.workers.dispatcher" in v for v in violations)


def test_drift_tolerates_small_hub_growth(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_metrics(monkeypatch, fan_in={"synthorg.core.types": 103})
    baseline = {
        "fan_in": {"synthorg.core.types": 100},
        "budget_pressure": {},
        "lcom": {},
    }
    assert _GATE.check(project_root=tmp_path, baseline=baseline) == []


def test_drift_fails_on_lcom_regression(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_metrics(monkeypatch, lcom={"synthorg.x:Svc": {"loc": 500, "lcom4": 4}})
    baseline = {
        "fan_in": {},
        "budget_pressure": {},
        "lcom": {"synthorg.x:Svc": {"loc": 480, "lcom4": 3}},
    }
    violations = _GATE.check(project_root=tmp_path, baseline=baseline)
    assert any("lcom" in v and "synthorg.x:Svc" in v for v in violations)


def test_drift_fails_on_new_low_cohesion_class(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_metrics(monkeypatch, lcom={"synthorg.x:Svc": {"loc": 500, "lcom4": 3}})
    violations = _GATE.check(project_root=tmp_path, baseline=_EMPTY_BASELINE)
    assert any("lcom" in v and "synthorg.x:Svc" in v for v in violations)


def test_drift_fails_on_new_budget_pressure_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_metrics(
        monkeypatch,
        budget={
            "src/synthorg/new.py": {
                "tier": "code",
                "loc": 470,
                "cap": 500,
                "ratio": 0.94,
            }
        },
    )
    violations = _GATE.check(project_root=tmp_path, baseline=_EMPTY_BASELINE)
    assert any(
        "budget-pressure" in v and "src/synthorg/new.py" in v for v in violations
    )


def test_main_exits_one_when_report_missing(tmp_path: Path) -> None:
    assert _GATE.main(["--project-root", str(tmp_path)]) == 1
