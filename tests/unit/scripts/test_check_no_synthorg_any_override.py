"""Self-tests for the ``no-synthorg-any-override`` regression gate.

Pins the gate contract: no ``[[tool.mypy.overrides]]`` block targeting a
``synthorg.*`` module may lift ``disallow_any_explicit`` (whether via
``disallow_any_explicit = false`` or an ``explicit-any`` entry in
``disable_error_code``). Only the ``tests.*`` override may lift the flag.
"""

import importlib.util
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GATE_PATH = _REPO_ROOT / "scripts" / "check_no_synthorg_any_override.py"


def _load_gate() -> object:
    spec = importlib.util.spec_from_file_location(
        "_no_synthorg_any_override_gate",
        _GATE_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _violations(toml_text: str) -> list[str]:
    gate = _load_gate()
    data = tomllib.loads(toml_text)
    result = gate.find_violations(data)  # type: ignore[attr-defined]
    assert isinstance(result, list)
    return result


def _write_pyproject(tmp_path: Path, toml_text: str) -> int:
    gate = _load_gate()
    (tmp_path / "pyproject.toml").write_text(toml_text, encoding="utf-8")
    rc = gate.main(["--repo-root", str(tmp_path)])  # type: ignore[attr-defined]
    assert isinstance(rc, int)
    return rc


_TESTS_ONLY = """
[[tool.mypy.overrides]]
module = "tests.*"
disallow_any_explicit = false
"""

_SYNTHORG_SINGLE = """
[[tool.mypy.overrides]]
module = "synthorg.engine.*"
disallow_any_explicit = false
"""

_SYNTHORG_LIST = """
[[tool.mypy.overrides]]
module = ["synthorg.api.*", "synthorg.meta.*"]
disallow_any_explicit = false
"""

_SYNTHORG_ENFORCED = """
[[tool.mypy.overrides]]
module = "synthorg.engine.*"
disallow_any_explicit = true
"""

_SYNTHORG_DISABLE_CODE = """
[[tool.mypy.overrides]]
module = "synthorg.api.*"
disable_error_code = ["explicit-any", "unused-awaitable"]
"""

_SYNTHORG_OTHER_DISABLE_CODE = """
[[tool.mypy.overrides]]
module = "synthorg.api.*"
disable_error_code = ["unused-awaitable"]
"""

_IGNORE_MISSING_ONLY = """
[[tool.mypy.overrides]]
module = "litellm.*"
ignore_missing_imports = true
"""


def test_tests_override_is_allowed() -> None:
    """The ``tests.*`` block lifting the flag is permitted."""
    assert _violations(_TESTS_ONLY) == []


def test_synthorg_single_module_override_is_violation() -> None:
    """A single ``synthorg.*`` module lifting the flag is flagged."""
    assert _violations(_SYNTHORG_SINGLE) == ["synthorg.engine.*"]


def test_synthorg_list_form_override_flags_each_pattern() -> None:
    """The list form of ``module`` is expanded; every synthorg pattern is flagged."""
    assert _violations(_SYNTHORG_LIST) == ["synthorg.api.*", "synthorg.meta.*"]


def test_synthorg_override_enforcing_the_flag_is_ignored() -> None:
    """A ``synthorg.*`` block setting the flag to ``true`` is not a violation."""
    assert _violations(_SYNTHORG_ENFORCED) == []


def test_synthorg_disable_error_code_explicit_any_is_violation() -> None:
    """Listing ``explicit-any`` in ``disable_error_code`` lifts the flag too."""
    assert _violations(_SYNTHORG_DISABLE_CODE) == ["synthorg.api.*"]


def test_synthorg_disable_error_code_without_explicit_any_is_ignored() -> None:
    """Disabling an unrelated error code does not lift ``disallow_any_explicit``."""
    assert _violations(_SYNTHORG_OTHER_DISABLE_CODE) == []


def test_unrelated_override_is_ignored() -> None:
    """An ``ignore_missing_imports`` override without the flag is ignored."""
    assert _violations(_IGNORE_MISSING_ONLY) == []


def test_no_overrides_section_is_clean() -> None:
    """A pyproject without any mypy overrides yields no violations."""
    assert _violations("[tool.mypy]\ndisallow_any_explicit = true\n") == []


def test_main_returns_zero_on_clean_pyproject(tmp_path: Path) -> None:
    """``main`` exits 0 when only the ``tests.*`` override lifts the flag."""
    assert _write_pyproject(tmp_path, _TESTS_ONLY) == 0


def test_main_returns_one_on_synthorg_override(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``main`` exits 1 and names the offending module on a synthorg override."""
    rc = _write_pyproject(tmp_path, _SYNTHORG_SINGLE)
    assert rc == 1
    stderr = capsys.readouterr().err
    assert "synthorg.engine.*" in stderr
    assert "disallow_any_explicit" in stderr


def test_main_returns_two_on_missing_pyproject(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``main`` exits 2 (config error) when ``pyproject.toml`` is absent."""
    gate = _load_gate()
    rc = gate.main(["--repo-root", str(tmp_path)])  # type: ignore[attr-defined]
    assert rc == 2
    assert "could not read pyproject.toml" in capsys.readouterr().err


def test_main_returns_two_on_unreadable_repo_root(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``main`` exits 2 when ``--repo-root`` is not a directory."""
    gate = _load_gate()
    missing = tmp_path / "does-not-exist"
    rc = gate.main(["--repo-root", str(missing)])  # type: ignore[attr-defined]
    assert rc == 2
    assert "not a directory" in capsys.readouterr().err


def test_real_pyproject_is_compliant() -> None:
    """The gate must be green against the actual repo pyproject (no regressions)."""
    gate = _load_gate()
    assert gate.main(["--repo-root", str(_REPO_ROOT)]) == 0  # type: ignore[attr-defined]
