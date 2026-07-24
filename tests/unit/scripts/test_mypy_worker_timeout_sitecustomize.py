"""Unit tests for ``scripts/_mypy_worker_timeout/sitecustomize.py``.

The hook widens mypy's hardcoded parallel-worker IPC timeouts at interpreter
startup. It has to stay inert for any interpreter that never imports mypy,
because it lands on ``PYTHONPATH`` for every subprocess the pre-push hook
spawns, not only the ones that type-check.
"""

import builtins
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_HOOK_PATH = _REPO_ROOT / "scripts" / "_mypy_worker_timeout" / "sitecustomize.py"


def _exec_hook() -> ModuleType:
    """Execute the hook in a fresh module namespace and return it.

    Executed rather than imported so each test observes the reassignment the
    hook performs at import time, which is the whole behaviour under test.
    """
    spec = importlib.util.spec_from_file_location("_sitecustomize_probe", _HOOK_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_widens_both_worker_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both ceilings must move: mypy aborts if either side gives up first."""
    fake_defaults = ModuleType("mypy.defaults")
    original = 1
    fake_defaults.WORKER_CONNECTION_TIMEOUT = original  # type: ignore[attr-defined]
    fake_defaults.WORKER_START_TIMEOUT = original  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mypy.defaults", fake_defaults)

    module = _exec_hook()

    widened = cast(Any, fake_defaults)  # type: ignore[explicit-any]  # stand-in module has no static attrs
    expected = module._WORKER_IPC_TIMEOUT_SECONDS
    assert expected == widened.WORKER_CONNECTION_TIMEOUT
    assert expected == widened.WORKER_START_TIMEOUT
    assert expected > original


def test_is_inert_without_mypy(monkeypatch: pytest.MonkeyPatch) -> None:
    """A subprocess that never imports mypy must not fail at startup."""
    real_import = builtins.__import__

    def _blocked(name: str, *args: object, **kwargs: object) -> ModuleType:
        if name.startswith("mypy"):
            message = "no module named mypy"
            raise ImportError(message)
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.delitem(sys.modules, "mypy.defaults", raising=False)
    monkeypatch.delitem(sys.modules, "mypy", raising=False)
    monkeypatch.setattr("builtins.__import__", _blocked)

    # Must not raise.
    _exec_hook()
