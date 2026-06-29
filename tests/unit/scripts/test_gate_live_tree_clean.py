"""Live-tree clean guard for the cost-scope-purpose and prompt-class-metadata gates.

The per-gate unit tests prove each gate flags a *synthetic* violation; this is
the complementary clean assertion (mirroring
``test_check_completion_config_temperature.test_clean_src_tree``): both
``check_cost_scope_purpose`` and ``check_prompt_class_metadata`` must return
``0`` against the real ``src/synthorg`` tree, so a prompt class that tags spend
without a purpose or a metadata property is caught at unit-test speed, not only
at pre-push / CI.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"


def _load_gate(name: str) -> ModuleType:
    # The gates prepend scripts/ to sys.path at import time to resolve their
    # shared _gate_source sibling; restore sys.path so the load leaves no global
    # side effect that could shadow an unrelated import.
    saved = sys.path[:]
    try:
        spec = importlib.util.spec_from_file_location(
            f"_gate_{name}", _SCRIPTS_DIR / f"{name}.py"
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = saved


def test_cost_scope_purpose_gate_clean_on_live_tree() -> None:
    gate = _load_gate("check_cost_scope_purpose")
    assert gate.main(["--repo-root", str(_REPO_ROOT)]) == 0


def test_prompt_class_metadata_gate_clean_on_live_tree() -> None:
    gate = _load_gate("check_prompt_class_metadata")
    assert gate.cmd_scan_all(_REPO_ROOT) == 0
