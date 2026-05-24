# mypy: disable-error-code="explicit-any"
"""Unit tests for ``scripts/check_strategy_protocol_injection.py``."""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_strategy_protocol_injection.py"


def _load_gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_check_strategy_protocol_injection", _SCRIPT_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_GATE: Any = cast("Any", _load_gate())


def _materialise(project: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        full = project / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")


# ── harvest_registered_classes ─────────────────────────────────


def test_harvests_registered_class_from_register_call(tmp_path: Path) -> None:
    _materialise(
        tmp_path,
        {
            "src/synthorg/foo/factory.py": (
                "from synthorg.foo.impl import ConcreteFoo\n"
                "Registry.register('default', ConcreteFoo)\n"
            ),
        },
    )
    registered = _GATE.harvest_registered_classes(tmp_path)
    assert "ConcreteFoo" in {entry.class_name for entry in registered}


def test_harvests_registered_class_via_register_strategy(tmp_path: Path) -> None:
    _materialise(
        tmp_path,
        {
            "src/synthorg/foo/factory.py": (
                "register_strategy('default', ConcreteImpl)\n"
            ),
        },
    )
    registered = _GATE.harvest_registered_classes(tmp_path)
    assert "ConcreteImpl" in {entry.class_name for entry in registered}


# ── find_callsite_violations ───────────────────────────────────


def test_finds_callsite_using_concrete_class_as_annotation(tmp_path: Path) -> None:
    _materialise(
        tmp_path,
        {
            "src/synthorg/foo/factory.py": (
                "from synthorg.foo.impl import ConcreteFoo\n"
                "Registry.register('default', ConcreteFoo)\n"
            ),
            "src/synthorg/bar/consumer.py": (
                "from synthorg.foo.impl import ConcreteFoo\n"
                "def use(foo: ConcreteFoo) -> None:\n"
                "    pass\n"
            ),
        },
    )
    baseline = tmp_path / "scripts" / "_strategy_protocol_injection_baseline.txt"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("", encoding="utf-8")
    violations = _GATE.check(project_root=tmp_path, baseline_path=baseline)
    assert len(violations) == 1
    assert "consumer.py" in violations[0].render()


def test_factory_file_itself_is_exempt(tmp_path: Path) -> None:
    """Registering file is allowed to reference the concrete class directly."""
    _materialise(
        tmp_path,
        {
            "src/synthorg/foo/factory.py": (
                "from synthorg.foo.impl import ConcreteFoo\n"
                "Registry.register('default', ConcreteFoo)\n"
                "def build(foo: ConcreteFoo) -> None:\n"
                "    pass\n"
            ),
        },
    )
    baseline = tmp_path / "scripts" / "_strategy_protocol_injection_baseline.txt"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("", encoding="utf-8")
    assert _GATE.check(project_root=tmp_path, baseline_path=baseline) == []


def test_baselined_violation_passes(tmp_path: Path) -> None:
    _materialise(
        tmp_path,
        {
            "src/synthorg/foo/factory.py": (
                "Registry.register('default', ConcreteFoo)\n"
            ),
            "src/synthorg/bar/consumer.py": (
                "def use(foo: ConcreteFoo) -> None:\n    pass\n"
            ),
        },
    )
    baseline = tmp_path / "scripts" / "_strategy_protocol_injection_baseline.txt"
    baseline.parent.mkdir(parents=True)
    baseline.write_text(
        "src/synthorg/bar/consumer.py:1:use:ConcreteFoo\n", encoding="utf-8"
    )
    assert _GATE.check(project_root=tmp_path, baseline_path=baseline) == []


# ── write_baseline ──────────────────────────────────────────────


def test_write_baseline_is_idempotent(tmp_path: Path) -> None:
    _materialise(
        tmp_path,
        {
            "src/synthorg/foo/factory.py": (
                "Registry.register('default', ConcreteFoo)\n"
            ),
            "src/synthorg/bar/consumer.py": (
                "def use(foo: ConcreteFoo) -> None:\n    pass\n"
            ),
        },
    )
    baseline = tmp_path / "scripts" / "_strategy_protocol_injection_baseline.txt"
    baseline.parent.mkdir(parents=True)
    _GATE.write_baseline(project_root=tmp_path, baseline_path=baseline)
    first = baseline.read_text(encoding="utf-8")
    _GATE.write_baseline(project_root=tmp_path, baseline_path=baseline)
    second = baseline.read_text(encoding="utf-8")
    assert first == second
