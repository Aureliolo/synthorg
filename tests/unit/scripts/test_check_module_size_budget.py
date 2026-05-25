# mypy: disable-error-code="explicit-any"
"""Unit tests for ``scripts/check_module_size_budget.py``.

Synthetic ``src/synthorg/`` trees under ``tmp_path`` exercise the gate's
tier-cap, baseline, and ``--update-baseline`` behaviour without
spawning subprocesses.
"""

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_module_size_budget.py"


def _load_gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_check_module_size_budget",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_GATE: Any = cast("Any", _load_gate())


def _write_src_file(
    project: Path, rel: str, *, lines: int, header: str | None = None
) -> Path:
    """Write a python file with *lines* counting-loc-eligible content.

    The optional *header* lands on the first line; a small ``import`` is
    used to seed each counted line so the file is syntactically valid
    Python (though parseability is not required by the gate).
    """
    src = project / rel
    src.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"x{i} = {i}" for i in range(lines))
    pieces: list[str] = []
    if header is not None:
        pieces.append(header)
    pieces.append(body)
    src.write_text("\n".join(pieces) + "\n", encoding="utf-8")
    return src


def _empty_baseline(path: Path) -> None:
    path.write_text("{}\n", encoding="utf-8")


# ── Default-tier behaviour ──────────────────────────────────────


def test_passes_when_no_files_exceed_cap(tmp_path: Path) -> None:
    project = tmp_path
    _write_src_file(project, "src/synthorg/foo.py", lines=100)
    baseline = project / "scripts" / "_module_size_baseline.json"
    baseline.parent.mkdir(parents=True)
    _empty_baseline(baseline)
    violations = _GATE.check(project_root=project, baseline_path=baseline)
    assert violations == []


def test_fails_when_default_tier_at_cap_plus_one(tmp_path: Path) -> None:
    project = tmp_path
    _write_src_file(project, "src/synthorg/foo.py", lines=501)
    baseline = project / "scripts" / "_module_size_baseline.json"
    baseline.parent.mkdir(parents=True)
    _empty_baseline(baseline)
    violations = _GATE.check(project_root=project, baseline_path=baseline)
    assert len(violations) == 1
    assert "src/synthorg/foo.py" in violations[0].render()


def test_passes_default_tier_exactly_at_cap(tmp_path: Path) -> None:
    project = tmp_path
    _write_src_file(project, "src/synthorg/foo.py", lines=500)
    baseline = project / "scripts" / "_module_size_baseline.json"
    baseline.parent.mkdir(parents=True)
    _empty_baseline(baseline)
    violations = _GATE.check(project_root=project, baseline_path=baseline)
    assert violations == []


# ── Headered tiers ──────────────────────────────────────────────


def test_controller_tier_cap_is_400(tmp_path: Path) -> None:
    project = tmp_path
    _write_src_file(
        project, "src/synthorg/api/c.py", lines=401, header="# module-kind: controller"
    )
    baseline = project / "scripts" / "_module_size_baseline.json"
    baseline.parent.mkdir(parents=True)
    _empty_baseline(baseline)
    violations = _GATE.check(project_root=project, baseline_path=baseline)
    assert len(violations) == 1


def test_service_tier_cap_is_600(tmp_path: Path) -> None:
    project = tmp_path
    _write_src_file(
        project, "src/synthorg/s.py", lines=600, header="# module-kind: service"
    )
    baseline = project / "scripts" / "_module_size_baseline.json"
    baseline.parent.mkdir(parents=True)
    _empty_baseline(baseline)
    assert _GATE.check(project_root=project, baseline_path=baseline) == []


def test_declarative_tier_is_exempt(tmp_path: Path) -> None:
    project = tmp_path
    _write_src_file(
        project, "src/synthorg/d.py", lines=5000, header="# module-kind: declarative"
    )
    baseline = project / "scripts" / "_module_size_baseline.json"
    baseline.parent.mkdir(parents=True)
    _empty_baseline(baseline)
    assert _GATE.check(project_root=project, baseline_path=baseline) == []


def test_generated_glob_skipped(tmp_path: Path) -> None:
    project = tmp_path
    _write_src_file(project, "src/synthorg/api/types.gen.py", lines=5000)
    _write_src_file(project, "src/synthorg/proto/foo_pb2.py", lines=5000)
    baseline = project / "scripts" / "_module_size_baseline.json"
    baseline.parent.mkdir(parents=True)
    _empty_baseline(baseline)
    assert _GATE.check(project_root=project, baseline_path=baseline) == []


# ── Baseline behaviour ──────────────────────────────────────────


def test_baselined_file_at_baseline_passes(tmp_path: Path) -> None:
    project = tmp_path
    _write_src_file(project, "src/synthorg/big.py", lines=2000)
    baseline = project / "scripts" / "_module_size_baseline.json"
    baseline.parent.mkdir(parents=True)
    baseline.write_text(json.dumps({"src/synthorg/big.py": 2000}), encoding="utf-8")
    assert _GATE.check(project_root=project, baseline_path=baseline) == []


def test_baselined_file_under_baseline_passes(tmp_path: Path) -> None:
    project = tmp_path
    _write_src_file(project, "src/synthorg/big.py", lines=1500)
    baseline = project / "scripts" / "_module_size_baseline.json"
    baseline.parent.mkdir(parents=True)
    baseline.write_text(json.dumps({"src/synthorg/big.py": 2000}), encoding="utf-8")
    assert _GATE.check(project_root=project, baseline_path=baseline) == []


def test_baselined_file_over_baseline_fails(tmp_path: Path) -> None:
    project = tmp_path
    _write_src_file(project, "src/synthorg/big.py", lines=2001)
    baseline = project / "scripts" / "_module_size_baseline.json"
    baseline.parent.mkdir(parents=True)
    baseline.write_text(json.dumps({"src/synthorg/big.py": 2000}), encoding="utf-8")
    violations = _GATE.check(project_root=project, baseline_path=baseline)
    assert len(violations) == 1
    assert "2001" in violations[0].render()


# ── Unknown tier ────────────────────────────────────────────────


def test_unknown_tier_header_raises(tmp_path: Path) -> None:
    project = tmp_path
    _write_src_file(
        project, "src/synthorg/f.py", lines=10, header="# module-kind: bogus"
    )
    baseline = project / "scripts" / "_module_size_baseline.json"
    baseline.parent.mkdir(parents=True)
    _empty_baseline(baseline)
    with pytest.raises(ValueError, match="bogus"):
        _GATE.check(project_root=project, baseline_path=baseline)


# ── --update-baseline ───────────────────────────────────────────


def test_update_baseline_writes_violators(tmp_path: Path) -> None:
    project = tmp_path
    _write_src_file(project, "src/synthorg/big.py", lines=750)
    _write_src_file(project, "src/synthorg/ok.py", lines=100)
    baseline = project / "scripts" / "_module_size_baseline.json"
    baseline.parent.mkdir(parents=True)
    _empty_baseline(baseline)
    _GATE.write_baseline(project_root=project, baseline_path=baseline)
    payload = json.loads(baseline.read_text(encoding="utf-8"))
    assert payload["locations"] == {"src/synthorg/big.py": 750}
    assert "description" in payload


def test_update_baseline_is_idempotent(tmp_path: Path) -> None:
    project = tmp_path
    _write_src_file(project, "src/synthorg/big.py", lines=750)
    baseline = project / "scripts" / "_module_size_baseline.json"
    baseline.parent.mkdir(parents=True)
    _empty_baseline(baseline)
    _GATE.write_baseline(project_root=project, baseline_path=baseline)
    first = baseline.read_text(encoding="utf-8")
    _GATE.write_baseline(project_root=project, baseline_path=baseline)
    second = baseline.read_text(encoding="utf-8")
    assert first == second


def test_update_baseline_writes_sorted_keys(tmp_path: Path) -> None:
    project = tmp_path
    _write_src_file(project, "src/synthorg/z.py", lines=750)
    _write_src_file(project, "src/synthorg/a.py", lines=750)
    _write_src_file(project, "src/synthorg/m.py", lines=750)
    baseline = project / "scripts" / "_module_size_baseline.json"
    baseline.parent.mkdir(parents=True)
    _empty_baseline(baseline)
    _GATE.write_baseline(project_root=project, baseline_path=baseline)
    payload = json.loads(baseline.read_text(encoding="utf-8"))
    assert list(payload["locations"].keys()) == [
        "src/synthorg/a.py",
        "src/synthorg/m.py",
        "src/synthorg/z.py",
    ]


# ── CLI main ────────────────────────────────────────────────────


def test_main_exits_zero_on_clean_tree(tmp_path: Path) -> None:
    project = tmp_path
    _write_src_file(project, "src/synthorg/f.py", lines=10)
    baseline = project / "scripts" / "_module_size_baseline.json"
    baseline.parent.mkdir(parents=True)
    _empty_baseline(baseline)
    exit_code = _GATE.main(
        ["--project-root", str(project), "--baseline", str(baseline)]
    )
    assert exit_code == 0


def test_main_exits_one_on_violation(tmp_path: Path) -> None:
    project = tmp_path
    _write_src_file(project, "src/synthorg/big.py", lines=600)
    baseline = project / "scripts" / "_module_size_baseline.json"
    baseline.parent.mkdir(parents=True)
    _empty_baseline(baseline)
    exit_code = _GATE.main(
        ["--project-root", str(project), "--baseline", str(baseline)]
    )
    assert exit_code == 1
