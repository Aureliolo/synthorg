"""Unit tests for ``scripts/check_no_ruff100_self_cloak.py``."""

import importlib.util
import shutil
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_no_ruff100_self_cloak.py"


@pytest.fixture(autouse=True)
def _isolate_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Confine every git call in this module to the per-test temp repos.

    ``git push`` exports ``GIT_DIR`` / ``GIT_INDEX_FILE`` / ``GIT_WORK_TREE``
    into the pre-push hook environment that pytest inherits. Those vars
    override directory-based repo discovery, so without stripping them a
    ``git add`` in a temp worktree (and the gate's own ``git ls-files``)
    would target the REAL repo index instead of ``cwd`` -- staging every
    real file as deleted, since none of them exist in the temp tree.
    """
    for var in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
    ):
        monkeypatch.delenv(var, raising=False)


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


def test_noqa_pattern_inside_string_literal_is_not_flagged(tmp_path: Path) -> None:
    # A self-cloak *pattern* embedded in a docstring/string is not a real
    # directive; comment-token scanning ignores it where a raw-text scan
    # would false-positive on the literal.
    f = _write(tmp_path / "a.py", f'DOC = """see {_noqa("F401,RUF100")}"""\n')
    assert _GATE._self_cloak_lines(f) == []


def test_self_cloak_in_unparseable_file_caught_via_fallback(tmp_path: Path) -> None:
    # An unclosed paren makes the file un-tokenisable; the raw-text
    # fallback still catches the comment-borne self-cloak.
    f = _write(tmp_path / "a.py", f"x = (  {_noqa('F401,RUF100')}\n")
    assert _GATE._self_cloak_lines(f) == [1]


# ── guard: cross-check the custom logic against ruff's runtime ──

_TARGET_CODE = "RUF100"


def _ruff_reports_unused_noqa(path: Path) -> bool:
    """Return whether ruff's RUF100 fires on *path*, isolated from repo config."""
    ruff = shutil.which("ruff")
    assert ruff is not None, "ruff must be installed to cross-check RUF100 semantics"
    result = subprocess.run(  # noqa: S603
        [
            ruff,
            "check",
            "--isolated",
            "--select",
            "F401,RUF100",
            "--output-format",
            "concise",
            "--no-cache",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return _TARGET_CODE in result.stdout


def test_guard_self_cloak_matches_ruff_runtime_behaviour(tmp_path: Path) -> None:
    """Pin the hand-rolled self-cloak semantics to ruff's actual RUF100 runtime.

    The gate reimplements RUF100's self-cloak semantics by hand; this guard
    cross-checks them against ruff itself, so a future ruff change to those
    semantics surfaces here instead of silently bypassing the custom logic.
    """
    # A noqa suppressing F401 on a non-import line is a dead directive.
    # Unpaired, ruff's RUF100 flags it -- and the gate stays silent (a lone
    # dead directive is ruff's job, not a self-cloak).
    uncloaked = _write(tmp_path / "uncloaked.py", f"x = 1  {_noqa('F401')}\n")
    assert _ruff_reports_unused_noqa(uncloaked)
    assert _GATE._self_cloak_lines(uncloaked) == []
    # Pairing RUF100 into the same directive cloaks it: ruff goes silent, so
    # ONLY the gate catches the now-hidden dead directive.
    cloaked = _write(tmp_path / "cloaked.py", f"x = 1  {_noqa('F401,RUF100')}\n")
    assert not _ruff_reports_unused_noqa(cloaked)
    assert _GATE._self_cloak_lines(cloaked) == [1]


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
