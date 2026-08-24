"""Unit tests for ``scripts/check_pin_golden_fresh.py``.

Exercises the recompute-vs-committed canary: the live pins must match the
committed golden (clean), a tampered fingerprint surfaces as drift, and an
extra golden entry for an unregistered purpose surfaces as stale.

The script is loaded via :mod:`importlib`, matching the sibling gate tests.
"""

import importlib.util
import json
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest

from synthorg.llm.pin_validation import GOLDEN_PATH

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_pin_golden_fresh.py"


class _ScriptModule(Protocol):
    """Subset of the script's surface the tests exercise."""

    @staticmethod
    def check(golden_path: Path | None = None) -> int: ...
    @staticmethod
    def main(argv: list[str] | None = None) -> int: ...


def _load_script() -> _ScriptModule:
    saved = sys.path[:]
    try:
        spec = importlib.util.spec_from_file_location(
            "_check_pin_golden_fresh",
            _SCRIPT_PATH,
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return cast("_ScriptModule", module)
    finally:
        sys.path[:] = saved


_MODULE = _load_script()


def test_committed_golden_is_fresh() -> None:
    # The committed golden must match the live pins; a failure here means a
    # pin changed without the regen tool being run.
    assert _MODULE.check() == 0


def test_stale_golden_detected(tmp_path: Path) -> None:
    committed = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    first_key = next(iter(sorted(committed)))
    committed[first_key] = "0" * 64
    tampered = tmp_path / "pin_golden.json"
    tampered.write_text(json.dumps(committed), encoding="utf-8")

    assert _MODULE.check(tampered) == 1


def test_extra_stale_entry_detected(tmp_path: Path) -> None:
    committed = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    committed["system:does-not-exist"] = "f" * 64
    tampered = tmp_path / "pin_golden.json"
    tampered.write_text(json.dumps(committed), encoding="utf-8")

    assert _MODULE.check(tampered) == 1


def test_new_live_class_missing_from_golden_detected(tmp_path: Path) -> None:
    # A purpose added to the registry/pins without regenerating the golden:
    # the live fingerprint exists but the committed golden lacks the key.
    committed = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    del committed[next(iter(sorted(committed)))]
    tampered = tmp_path / "pin_golden.json"
    tampered.write_text(json.dumps(committed), encoding="utf-8")

    assert _MODULE.check(tampered) == 1


def test_malformed_golden_reports_clean_failure(tmp_path: Path) -> None:
    tampered = tmp_path / "pin_golden.json"
    tampered.write_text("{not valid json", encoding="utf-8")

    assert _MODULE.check(tampered) == 1
