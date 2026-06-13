"""Tests for ``scripts/check_completion_config_temperature.py``.

Verifies the AST gate flags ``CompletionConfig(...)`` calls that omit a real
temperature (absent or ``None``) and accepts every explicit form: a literal, a
named constant, an attribute drawn from config, and a ``**kwargs`` spread it
cannot statically resolve.
"""

import importlib.util
import sys
import types
from pathlib import Path
from typing import cast

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_completion_config_temperature.py"


def _load_script_module() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_check_completion_config_temperature",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


_MOD = _load_script_module()


def _scan(tmp_path: Path, source: str) -> list[int]:
    sample = tmp_path / "sample.py"
    sample.write_text(source, encoding="utf-8")
    return cast("list[int]", _MOD._scan_file(sample))


@pytest.mark.unit
class TestCompletionConfigTemperatureGate:
    """The gate accepts explicit temperatures and rejects omissions."""

    def test_missing_temperature_is_flagged(self, tmp_path: Path) -> None:
        hits = _scan(tmp_path, "x = CompletionConfig(max_tokens=256)\n")
        assert hits == [1]

    def test_none_temperature_is_flagged(self, tmp_path: Path) -> None:
        hits = _scan(tmp_path, "x = CompletionConfig(temperature=None)\n")
        assert hits == [1]

    def test_literal_temperature_passes(self, tmp_path: Path) -> None:
        assert _scan(tmp_path, "x = CompletionConfig(temperature=0.0)\n") == []

    def test_named_constant_temperature_passes(self, tmp_path: Path) -> None:
        source = "x = CompletionConfig(temperature=_PINNED, max_tokens=8)\n"
        assert _scan(tmp_path, source) == []

    def test_config_sourced_temperature_passes(self, tmp_path: Path) -> None:
        source = "x = CompletionConfig(temperature=self._config.temperature)\n"
        assert _scan(tmp_path, source) == []

    def test_kwargs_spread_is_skipped(self, tmp_path: Path) -> None:
        # The mapping may carry temperature; the AST cannot resolve it, so the
        # gate must not false-flag.
        assert _scan(tmp_path, "x = CompletionConfig(**opts)\n") == []

    def test_clean_src_tree(self) -> None:
        """The gate passes on the current source tree (regression guard)."""
        assert _MOD.cmd_scan_all() == 0
