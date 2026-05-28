"""Tests for the AppState attribute lock.

The gate forbids growing :class:`AppState`'s ``__slots__`` past the
hard-coded approved set so new application state goes through a feature
state slice instead of being bolted onto the composition root.
"""

import importlib.util
import textwrap
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "check_no_implicit_state_attribute.py"


class _GateModule(Protocol):
    """Subset of ``scripts/check_no_implicit_state_attribute.py``."""

    APPROVED_SLOTS: frozenset[str]

    @staticmethod
    def extract_slots(state_py: Path) -> frozenset[str]: ...
    @staticmethod
    def check(*, state_py: Path) -> list[str]: ...


def _load() -> _GateModule:
    spec = importlib.util.spec_from_file_location(
        "check_no_implicit_state_attribute", _SCRIPT
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_GateModule, module)


_GATE = _load()


def _write_app_state(tmp_path: Path, *, slot_names: tuple[str, ...]) -> Path:
    """Write a stub ``state.py`` declaring ``AppState`` with *slot_names*."""
    formatted = ",\n        ".join(f'"{name}"' for name in slot_names)
    body = textwrap.dedent(
        f'''\
        """Stub AppState for gate tests."""


        class AppState:
            """Test stub."""

            __slots__ = (
                {formatted},
            )
        '''
    )
    target = tmp_path / "state.py"
    target.write_text(body, encoding="utf-8")
    return target


def test_extract_slots_returns_declared_slots(tmp_path: Path) -> None:
    state_py = _write_app_state(tmp_path, slot_names=("foo", "bar", "baz"))
    assert _GATE.extract_slots(state_py) == frozenset({"foo", "bar", "baz"})


def test_gate_passes_when_slots_match_approved(tmp_path: Path) -> None:
    approved = tuple(sorted(_GATE.APPROVED_SLOTS))
    state_py = _write_app_state(tmp_path, slot_names=approved)
    findings = _GATE.check(state_py=state_py)
    assert findings == []


def test_gate_fails_on_added_slot(tmp_path: Path) -> None:
    grown = tuple(sorted({*_GATE.APPROVED_SLOTS, "new_attribute"}))
    state_py = _write_app_state(tmp_path, slot_names=grown)
    findings = _GATE.check(state_py=state_py)
    assert any("new_attribute" in f for f in findings)


def test_gate_fails_on_removed_slot(tmp_path: Path) -> None:
    if not _GATE.APPROVED_SLOTS:
        pytest.skip("approved set empty; skip removal test")
    shrunk = tuple(sorted(_GATE.APPROVED_SLOTS))[1:]
    state_py = _write_app_state(tmp_path, slot_names=shrunk)
    findings = _GATE.check(state_py=state_py)
    assert findings, "removing an approved slot should fail the gate"


def test_approved_set_matches_current_app_state() -> None:
    """The hard-coded set is the source of truth for the production AppState."""
    repo_state_py = _REPO_ROOT / "src" / "synthorg" / "api" / "state.py"
    actual = _GATE.extract_slots(repo_state_py)
    assert actual == _GATE.APPROVED_SLOTS
