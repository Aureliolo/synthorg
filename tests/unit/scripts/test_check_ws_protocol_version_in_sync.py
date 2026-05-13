"""Unit tests for ``scripts/check_ws_protocol_version_in_sync.py``.

Invokes the script as a subprocess from a temporary worktree that
overrides the two source files, asserts the exit code and stderr
behaviour for matched / mismatched / missing-version cases.
"""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "check_ws_protocol_version_in_sync.py"


def _write_pair(
    tmp_root: Path,
    *,
    py_body: str | None,
    ts_body: str | None,
) -> None:
    py_path = tmp_root / "src" / "synthorg" / "api" / "ws_models.py"
    ts_path = tmp_root / "web" / "src" / "utils" / "constants.ts"
    py_path.parent.mkdir(parents=True, exist_ok=True)
    ts_path.parent.mkdir(parents=True, exist_ok=True)
    if py_body is not None:
        py_path.write_text(py_body, encoding="utf-8")
    if ts_body is not None:
        ts_path.write_text(ts_body, encoding="utf-8")


def _run(tmp_root: Path) -> subprocess.CompletedProcess[str]:
    script_copy = tmp_root / "scripts" / "check_ws_protocol_version_in_sync.py"
    script_copy.parent.mkdir(parents=True, exist_ok=True)
    script_copy.write_text(_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    return subprocess.run(  # noqa: S603
        [sys.executable, str(script_copy)],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )


def test_passes_when_versions_match(tmp_path: Path) -> None:
    _write_pair(
        tmp_path,
        py_body="WS_PROTOCOL_VERSION: int = 3\n",
        ts_body="export const WS_PROTOCOL_VERSION = 3\n",
    )
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr


def test_fails_on_version_mismatch(tmp_path: Path) -> None:
    _write_pair(
        tmp_path,
        py_body="WS_PROTOCOL_VERSION: int = 2\n",
        ts_body="export const WS_PROTOCOL_VERSION = 3\n",
    )
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "drift" in result.stderr.lower()


def test_fails_when_python_file_missing(tmp_path: Path) -> None:
    _write_pair(
        tmp_path,
        py_body=None,
        ts_body="export const WS_PROTOCOL_VERSION = 1\n",
    )
    result = _run(tmp_path)
    assert result.returncode == 1


def test_fails_when_declaration_missing(tmp_path: Path) -> None:
    _write_pair(
        tmp_path,
        py_body="# no version constant here\n",
        ts_body="export const WS_PROTOCOL_VERSION = 1\n",
    )
    result = _run(tmp_path)
    assert result.returncode == 1


def test_repo_state_is_in_sync() -> None:
    """The committed Python and TypeScript versions must agree."""
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        cwd=str(_REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"WS_PROTOCOL_VERSION drift detected: {result.stderr}"
    )
