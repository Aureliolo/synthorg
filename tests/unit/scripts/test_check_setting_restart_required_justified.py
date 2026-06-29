"""Tests for the restart-required-justification gate.

The gate fails when a ``restart_required=True`` / ``read_only_post_init=True``
setting is neither baselined nor marker-justified. These tests drive it against
a synthetic ``settings/definitions/`` tree so a deliberately mis-flagged
definition makes the check fail (and a marked / baselined one passes).
"""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GATE_PATH = _REPO_ROOT / "scripts" / "check_setting_restart_required_justified.py"


def _load_gate() -> ModuleType:
    """Import the gate script as a module by file path."""
    spec = importlib.util.spec_from_file_location("_restart_gate", _GATE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_GATE = _load_gate()


def _make_definitions(tmp_path: Path, body: str) -> Path:
    """Write a synthetic definitions module under a fake repo root."""
    defs = tmp_path / "src" / "synthorg" / "settings" / "definitions"
    defs.mkdir(parents=True)
    (defs / "__init__.py").write_text("", encoding="utf-8")
    (defs / "example.py").write_text(body, encoding="utf-8")
    return tmp_path


_MISFLAGGED = """\
_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="example_knob",
        restart_required=True,
    )
)
"""

_MARKED = """\
_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="example_knob",
        restart_required=True,
    )
)  # lint-allow: restart-required -- bound OS resource, cannot hot-reload
"""

_MARKED_NO_REASON = """\
_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="example_knob",
        restart_required=True,
    )
)  # lint-allow: restart-required
"""

_READ_ONLY = """\
_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="example_knob",
        read_only_post_init=True,
    )
)
"""

_HOT = """\
_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="example_knob",
    )
)
"""


def test_misflagged_definition_is_unjustified(tmp_path: Path) -> None:
    """A new restart_required setting with no marker / baseline fails."""
    repo = _make_definitions(tmp_path, _MISFLAGGED)
    records = _GATE.scan_definitions(repo)
    unjustified, stale = _GATE.evaluate(records, baseline=set())
    assert [r.setting_key for r in unjustified] == ["api.example_knob"]
    assert stale == set()


def test_marker_justifies_restart_required(tmp_path: Path) -> None:
    """A `# lint-allow: restart-required -- <reason>` marker passes the gate."""
    repo = _make_definitions(tmp_path, _MARKED)
    records = _GATE.scan_definitions(repo)
    assert records[0].has_marker is True
    unjustified, _stale = _GATE.evaluate(records, baseline=set())
    assert unjustified == []


def test_reason_less_marker_does_not_justify(tmp_path: Path) -> None:
    """A bare `# lint-allow: restart-required` (no ` -- <reason>`) is unjustified.

    The marker must carry a reason; otherwise a developer could satisfy the
    gate without stating why the setting is restart-bound.
    """
    repo = _make_definitions(tmp_path, _MARKED_NO_REASON)
    records = _GATE.scan_definitions(repo)
    assert records[0].has_marker is False
    unjustified, _stale = _GATE.evaluate(records, baseline=set())
    assert [r.setting_key for r in unjustified] == ["api.example_knob"]


def test_baseline_entry_justifies_restart_required(tmp_path: Path) -> None:
    """A baselined key passes without a marker."""
    repo = _make_definitions(tmp_path, _MISFLAGGED)
    records = _GATE.scan_definitions(repo)
    unjustified, stale = _GATE.evaluate(records, baseline={"api.example_knob"})
    assert unjustified == []
    assert stale == set()


def test_read_only_post_init_is_restart_bound(tmp_path: Path) -> None:
    """read_only_post_init implies restart-bound and is flagged."""
    repo = _make_definitions(tmp_path, _READ_ONLY)
    records = _GATE.scan_definitions(repo)
    assert [r.setting_key for r in records] == ["api.example_knob"]


def test_hot_setting_is_not_restart_bound(tmp_path: Path) -> None:
    """A setting with neither flag is not scanned as restart-bound."""
    repo = _make_definitions(tmp_path, _HOT)
    assert _GATE.scan_definitions(repo) == []


def test_stale_baseline_entry_warns_but_passes(tmp_path: Path) -> None:
    """A baseline key with no matching record is stale (warn, not fail)."""
    repo = _make_definitions(tmp_path, _HOT)
    records = _GATE.scan_definitions(repo)
    unjustified, stale = _GATE.evaluate(records, baseline={"api.removed_knob"})
    assert unjustified == []
    assert stale == {"api.removed_knob"}


def test_real_repo_passes() -> None:
    """The committed repo + baseline must satisfy the gate (exit 0)."""
    assert _GATE.main([]) == 0
