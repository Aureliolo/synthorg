"""Self-tests for the project-wide ``frozen-extra-forbid`` gate.

Pins the gate contract: every frozen ``ConfigDict`` model needs
``extra="forbid"`` unless it declares a ``@computed_field`` (automatic
section-8 carve-out) or carries a reasoned per-line opt-out.
"""

import importlib.util
from pathlib import Path
from typing import cast

import pytest

pytestmark = pytest.mark.unit

_GATE_PATH = (
    Path(__file__).resolve().parents[3]
    / "scripts"
    / "check_frozen_model_extra_forbid.py"
)


def _load_gate() -> object:
    spec = importlib.util.spec_from_file_location(
        "_frozen_extra_forbid_gate",
        _GATE_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _walk(tmp_path: Path, source: str) -> list[tuple[Path, int, str]]:
    gate = _load_gate()
    target = tmp_path / "mod.py"
    target.write_text(source, encoding="utf-8")
    result = gate._walk(target)  # type: ignore[attr-defined]
    return cast("list[tuple[Path, int, str]]", result)


def test_frozen_with_forbid_passes(tmp_path: Path) -> None:
    src = (
        "from pydantic import BaseModel, ConfigDict\n\n\n"
        "class Ok(BaseModel):\n"
        '    model_config = ConfigDict(frozen=True, extra="forbid")\n'
    )
    assert _walk(tmp_path, src) == []


def test_frozen_without_forbid_is_violation(tmp_path: Path) -> None:
    src = (
        "from pydantic import BaseModel, ConfigDict\n\n\n"
        "class Bad(BaseModel):\n"
        "    model_config = ConfigDict(frozen=True)\n"
    )
    violations = _walk(tmp_path, src)
    assert len(violations) == 1
    assert violations[0][2] == "Bad"


def test_computed_field_is_auto_exempt(tmp_path: Path) -> None:
    src = (
        "from pydantic import BaseModel, ConfigDict, computed_field\n\n\n"
        "class Derived(BaseModel):\n"
        "    model_config = ConfigDict(frozen=True)\n\n"
        "    @computed_field\n"
        "    @property\n"
        "    def x(self) -> int:\n"
        "        return 1\n"
    )
    assert _walk(tmp_path, src) == []


def test_optout_with_reason_passes(tmp_path: Path) -> None:
    src = (
        "from pydantic import BaseModel, ConfigDict\n\n\n"
        "class Allowed(BaseModel):  "
        "# lint-allow: frozen-extra-forbid -- provider keys vary\n"
        '    model_config = ConfigDict(frozen=True, extra="allow")\n'
    )
    assert _walk(tmp_path, src) == []


def test_bare_optout_is_violation(tmp_path: Path) -> None:
    src = (
        "from pydantic import BaseModel, ConfigDict\n\n\n"
        "class BareOptOut(BaseModel):  # lint-allow: frozen-extra-forbid\n"
        '    model_config = ConfigDict(frozen=True, extra="allow")\n'
    )
    violations = _walk(tmp_path, src)
    assert len(violations) == 1
    assert violations[0][2] == "BareOptOut"


def test_non_frozen_model_is_ignored(tmp_path: Path) -> None:
    src = (
        "from pydantic import BaseModel, ConfigDict\n\n\n"
        "class Mutable(BaseModel):\n"
        "    model_config = ConfigDict(frozen=False)\n"
    )
    assert _walk(tmp_path, src) == []


def test_last_write_wins(tmp_path: Path) -> None:
    """A class cannot strict-config early then override it later."""
    src = (
        "from pydantic import BaseModel, ConfigDict\n\n\n"
        "class Sneaky(BaseModel):\n"
        '    model_config = ConfigDict(frozen=True, extra="forbid")\n'
        "    model_config = ConfigDict(frozen=True)\n"
    )
    violations = _walk(tmp_path, src)
    assert len(violations) == 1
    assert violations[0][2] == "Sneaky"


def test_main_scope_includes_both_src_and_tests() -> None:
    """The project-wide gate walks src/synthorg/ AND tests/."""
    gate = _load_gate()
    assert gate.SRC_DIR.name == "synthorg"  # type: ignore[attr-defined]
    assert gate.TEST_DIR.name == "tests"  # type: ignore[attr-defined]
    assert gate.SRC_DIR.is_dir()  # type: ignore[attr-defined]
    assert gate.TEST_DIR.is_dir()  # type: ignore[attr-defined]


def test_main_walks_both_dirs_and_reports_combined_violations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``main()`` scans both SRC_DIR and TEST_DIR and merges violations.

    Builds two synthetic trees with a violating frozen model each,
    points the gate's scan roots at them, and verifies the combined
    output names BOTH classes. Guards against a refactor that
    accidentally collapses the dual-root loop to a single root.
    """
    gate = _load_gate()
    fake_src = tmp_path / "src" / "synthorg"
    fake_tests = tmp_path / "tests"
    fake_src.mkdir(parents=True)
    fake_tests.mkdir(parents=True)
    src_violator = fake_src / "mod_src.py"
    src_violator.write_text(
        "from pydantic import BaseModel, ConfigDict\n\n\n"
        "class BadSrcModel(BaseModel):\n"
        "    model_config = ConfigDict(frozen=True)\n",
        encoding="utf-8",
    )
    test_violator = fake_tests / "mod_test.py"
    test_violator.write_text(
        "from pydantic import BaseModel, ConfigDict\n\n\n"
        "class BadTestModel(BaseModel):\n"
        "    model_config = ConfigDict(frozen=True)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "SRC_DIR", fake_src)
    monkeypatch.setattr(gate, "TEST_DIR", fake_tests)
    # ``main()`` calls ``path.relative_to(REPO_ROOT)`` when printing
    # violations; re-anchor it so the synthetic paths resolve.
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    rc = gate.main()  # type: ignore[attr-defined]
    assert rc == 1
    stderr = capsys.readouterr().err
    assert "BadSrcModel" in stderr
    assert "BadTestModel" in stderr


def test_walk_prefilter_skips_files_without_model_config_token(
    tmp_path: Path,
) -> None:
    """A file with no ``model_config`` token must short-circuit.

    The fast pre-filter (`if "model_config" not in source: return []`)
    is the optimisation that drops gate runtime from ~7s to ~1.5s on
    the full src + tests tree. A regression that removes or inverts
    the check would silently slow every commit and pre-push.
    """
    gate = _load_gate()
    target = tmp_path / "no_config.py"
    # Realistic-looking module with imports + a class, but no Pydantic
    # config: should be invisible to the gate.
    target.write_text(
        "from dataclasses import dataclass\n\n\n"
        "@dataclass\n"
        "class Plain:\n"
        "    name: str\n",
        encoding="utf-8",
    )
    assert gate._walk(target) == []  # type: ignore[attr-defined]


def test_walk_prefilter_does_not_skip_files_with_model_config_token(
    tmp_path: Path,
) -> None:
    """Files containing ``model_config`` are fully parsed."""
    gate = _load_gate()
    target = tmp_path / "has_config.py"
    target.write_text(
        "from pydantic import BaseModel, ConfigDict\n\n\n"
        "class HasConfig(BaseModel):\n"
        "    model_config = ConfigDict(frozen=True)\n",
        encoding="utf-8",
    )
    violations = gate._walk(target)  # type: ignore[attr-defined]
    assert len(violations) == 1
    assert violations[0][2] == "HasConfig"


def test_real_codebase_is_compliant() -> None:
    """The gate must be green against the actual tree (no regressions)."""
    gate = _load_gate()
    assert gate.main() == 0  # type: ignore[attr-defined]
