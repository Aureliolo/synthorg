# mypy: disable-error-code="explicit-any"
"""Unit tests for ``scripts/check_no_circular_imports.py``."""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_no_circular_imports.py"


def _load_gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_check_no_circular_imports",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_GATE: Any = cast("Any", _load_gate())


# ── Cycle detection on synthetic graphs ─────────────────────────


def test_no_cycles_linear_chain() -> None:
    graph = {"a": {"b"}, "b": {"c"}, "c": set()}
    assert _GATE.find_cycles(graph) == []


def test_two_node_cycle() -> None:
    graph = {"a": {"b"}, "b": {"a"}}
    cycles = _GATE.find_cycles(graph)
    assert cycles == [("a", "b")]


def test_three_node_cycle() -> None:
    graph = {"a": {"b"}, "b": {"c"}, "c": {"a"}}
    cycles = _GATE.find_cycles(graph)
    assert cycles == [("a", "b", "c")]


def test_cycle_canonical_rotation_starts_with_smallest() -> None:
    graph = {"c": {"a"}, "a": {"b"}, "b": {"c"}}
    cycles = _GATE.find_cycles(graph)
    assert cycles == [("a", "b", "c")]


def test_self_loop_skipped() -> None:
    """A module that 'imports itself' is a parser oddity, not a real cycle."""
    graph = {"a": {"a"}}
    assert _GATE.find_cycles(graph) == []


def test_disjoint_cycles_both_reported() -> None:
    graph = {
        "a": {"b"},
        "b": {"a"},
        "c": {"d"},
        "d": {"c"},
    }
    cycles = _GATE.find_cycles(graph)
    assert ("a", "b") in cycles
    assert ("c", "d") in cycles
    assert len(cycles) == 2


# ── AST-based import extraction ─────────────────────────────────


def test_extract_imports_top_level(tmp_path: Path) -> None:
    src = (
        "import synthorg.foo\n"
        "from synthorg.bar import Baz\n"
        "from synthorg.qux import (a, b)\n"
    )
    path = tmp_path / "x.py"
    path.write_text(src, encoding="utf-8")
    imports = _GATE.extract_imports(path)
    assert "synthorg.foo" in imports
    assert "synthorg.bar" in imports
    assert "synthorg.qux" in imports


def test_extract_imports_skips_type_checking_block(tmp_path: Path) -> None:
    src = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from synthorg.only_for_types import Thing\n"
    )
    path = tmp_path / "x.py"
    path.write_text(src, encoding="utf-8")
    imports = _GATE.extract_imports(path)
    assert "synthorg.only_for_types" not in imports


def test_extract_imports_skips_function_local(tmp_path: Path) -> None:
    src = "def use_thing():\n    from synthorg.lazy import Thing\n    return Thing()\n"
    path = tmp_path / "x.py"
    path.write_text(src, encoding="utf-8")
    imports = _GATE.extract_imports(path)
    assert "synthorg.lazy" not in imports


# ── Module-path resolver ────────────────────────────────────────


@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        ("src/synthorg/foo.py", "synthorg.foo"),
        ("src/synthorg/foo/__init__.py", "synthorg.foo"),
        ("src/synthorg/foo/bar.py", "synthorg.foo.bar"),
        ("src/synthorg/__init__.py", "synthorg"),
    ],
)
def test_module_path_for_rel(rel: str, expected: str) -> None:
    assert _GATE.module_path_for_rel(rel) == expected


# ── End-to-end check on synthetic tree ──────────────────────────


def _materialise(project: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        full = project / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")


def test_check_passes_on_acyclic_tree(tmp_path: Path) -> None:
    _materialise(
        tmp_path,
        {
            "src/synthorg/__init__.py": "",
            "src/synthorg/a.py": "import synthorg.b\n",
            "src/synthorg/b.py": "import synthorg.c\n",
            "src/synthorg/c.py": "",
        },
    )
    baseline = tmp_path / "scripts" / "_circular_imports_baseline.txt"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("", encoding="utf-8")
    result = _GATE.check(project_root=tmp_path, baseline_path=baseline)
    assert result.is_clean()
    assert result.new_cycles == ()
    assert result.stale_baseline == ()


def test_check_finds_two_node_cycle(tmp_path: Path) -> None:
    _materialise(
        tmp_path,
        {
            "src/synthorg/__init__.py": "",
            "src/synthorg/a.py": "import synthorg.b\n",
            "src/synthorg/b.py": "import synthorg.a\n",
        },
    )
    baseline = tmp_path / "scripts" / "_circular_imports_baseline.txt"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("", encoding="utf-8")
    result = _GATE.check(project_root=tmp_path, baseline_path=baseline)
    assert len(result.new_cycles) == 1
    assert result.stale_baseline == ()


def test_check_baselined_cycle_passes(tmp_path: Path) -> None:
    _materialise(
        tmp_path,
        {
            "src/synthorg/__init__.py": "",
            "src/synthorg/a.py": "import synthorg.b\n",
            "src/synthorg/b.py": "import synthorg.a\n",
        },
    )
    baseline = tmp_path / "scripts" / "_circular_imports_baseline.txt"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("synthorg.a -> synthorg.b\n", encoding="utf-8")
    result = _GATE.check(project_root=tmp_path, baseline_path=baseline)
    assert result.is_clean()


def test_check_flags_stale_baseline_entry(tmp_path: Path) -> None:
    """Baseline cycle no longer in the graph must surface as stale + fail."""
    _materialise(
        tmp_path,
        {
            "src/synthorg/__init__.py": "",
            "src/synthorg/a.py": "import synthorg.b\n",
            "src/synthorg/b.py": "",
        },
    )
    baseline = tmp_path / "scripts" / "_circular_imports_baseline.txt"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("synthorg.a -> synthorg.b\n", encoding="utf-8")
    result = _GATE.check(project_root=tmp_path, baseline_path=baseline)
    assert result.new_cycles == ()
    assert result.stale_baseline == (("synthorg.a", "synthorg.b"),)
    assert not result.is_clean()


def test_main_exit_nonzero_on_stale_baseline_only(tmp_path: Path) -> None:
    """Stale baseline alone is enough to fail; new cycles aren't required."""
    _materialise(
        tmp_path,
        {
            "src/synthorg/__init__.py": "",
            "src/synthorg/a.py": "import synthorg.b\n",
            "src/synthorg/b.py": "",
        },
    )
    baseline = tmp_path / "scripts" / "_circular_imports_baseline.txt"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("synthorg.a -> synthorg.b\n", encoding="utf-8")
    exit_code = _GATE.main(
        [
            "--project-root",
            str(tmp_path),
            "--baseline",
            str(baseline),
        ]
    )
    assert exit_code == 1


# ── Baseline writer is idempotent ───────────────────────────────


def test_write_baseline_is_idempotent(tmp_path: Path) -> None:
    _materialise(
        tmp_path,
        {
            "src/synthorg/__init__.py": "",
            "src/synthorg/a.py": "import synthorg.b\n",
            "src/synthorg/b.py": "import synthorg.a\n",
        },
    )
    baseline = tmp_path / "scripts" / "_circular_imports_baseline.txt"
    baseline.parent.mkdir(parents=True)
    _GATE.write_baseline(project_root=tmp_path, baseline_path=baseline)
    first = baseline.read_text(encoding="utf-8")
    _GATE.write_baseline(project_root=tmp_path, baseline_path=baseline)
    second = baseline.read_text(encoding="utf-8")
    assert first == second
