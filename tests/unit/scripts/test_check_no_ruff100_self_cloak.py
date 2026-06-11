"""Unit tests for ``scripts/check_no_ruff100_self_cloak.py``."""

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_no_ruff100_self_cloak.py"

# The suppression token is assembled from fragments so the RUF100-paired
# fixtures below are not themselves flagged by the gate under test, which
# scans raw source text (string literals included). At runtime ``_noqa``
# produces a genuine directive in the temp files the gate then inspects.
_NOQA_TOKEN = "# " + "noqa"


def _noqa(codes: str) -> str:
    """Return a suppression directive (``hash`` + ``noqa: <codes>``) at runtime."""
    return f"{_NOQA_TOKEN}: {codes}"


def _load_gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_check_no_ruff100_self_cloak",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_GATE: Any = cast("Any", _load_gate())  # type: ignore[explicit-any]  # dynamically loaded gate module; attrs resolved by name


def _write(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


# ── _self_cloak_lines (core detection, pure) ────────────────────


def test_single_code_noqa_is_clean(tmp_path: Path) -> None:
    f = _write(tmp_path / "a.py", f"import os  {_noqa('F401')}\n")
    assert _GATE._self_cloak_lines(f) == []


def test_ruff100_alone_is_clean(tmp_path: Path) -> None:
    # RUF100 on its own is a real (if redundant) directive, not a self-cloak.
    f = _write(tmp_path / "a.py", f"x = 1  {_noqa('RUF100')}\n")
    assert _GATE._self_cloak_lines(f) == []


def test_ruff100_paired_is_a_self_cloak(tmp_path: Path) -> None:
    f = _write(tmp_path / "a.py", f"import os  {_noqa('RUF100,F401')}\n")
    assert _GATE._self_cloak_lines(f) == [1]


def test_self_cloak_detected_regardless_of_order(tmp_path: Path) -> None:
    f = _write(tmp_path / "a.py", f"import os  {_noqa('F401,RUF100')}\n")
    assert _GATE._self_cloak_lines(f) == [1]


def test_self_cloak_detected_with_trailing_reason(tmp_path: Path) -> None:
    f = _write(tmp_path / "a.py", f"import os  {_noqa('TC001,RUF100')} -- why\n")
    assert _GATE._self_cloak_lines(f) == [1]


def test_no_noqa_is_clean(tmp_path: Path) -> None:
    f = _write(tmp_path / "a.py", "x = 1\ny = 2\n")
    assert _GATE._self_cloak_lines(f) == []


def test_multiple_codes_without_ruff100_is_clean(tmp_path: Path) -> None:
    f = _write(tmp_path / "a.py", f"import os  {_noqa('F401,E501')}\n")
    assert _GATE._self_cloak_lines(f) == []


def test_reports_every_offending_line(tmp_path: Path) -> None:
    f = _write(
        tmp_path / "a.py",
        f"import os  {_noqa('RUF100,F401')}\n"
        "x = 1\n"
        f"import sys  {_noqa('F401,RUF100')}\n",
    )
    assert _GATE._self_cloak_lines(f) == [1, 3]


# ── _scan / main (end-to-end over a git repo) ───────────────────


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)  # noqa: S607
    (root / "clean.py").write_text(f"import os  {_noqa('F401')}\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)  # noqa: S607


def test_scan_passes_on_clean_repo(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    assert _GATE.main(["--repo-root", str(tmp_path)]) == 0


def test_scan_fails_on_self_cloak_in_repo(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "bad.py").write_text(
        f"import os  {_noqa('TC001,RUF100')}\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)  # noqa: S607
    assert _GATE.main(["--repo-root", str(tmp_path)]) == 1


def test_scan_ignores_untracked_self_cloak(tmp_path: Path) -> None:
    # The gate scans ``git ls-files``; an untracked self-cloak is invisible
    # until it is staged, matching how the directive would reach a commit.
    _init_repo(tmp_path)
    (tmp_path / "untracked.py").write_text(
        f"import os  {_noqa('TC001,RUF100')}\n", encoding="utf-8"
    )
    assert _GATE.main(["--repo-root", str(tmp_path)]) == 0
