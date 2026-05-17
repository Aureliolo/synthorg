"""Self-tests for the project-wide ``frozen-extra-forbid`` gate.

Pins the gate contract: every frozen ``ConfigDict`` model needs
``extra="forbid"`` unless it declares a ``@computed_field`` (automatic
section-8 carve-out) or carries a reasoned per-line opt-out.
"""

import importlib.util
from pathlib import Path

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
    return gate._walk(target)  # type: ignore[attr-defined]


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


def test_real_codebase_is_compliant() -> None:
    """The gate must be green against the actual tree (no regressions)."""
    gate = _load_gate()
    assert gate.main() == 0  # type: ignore[attr-defined]
