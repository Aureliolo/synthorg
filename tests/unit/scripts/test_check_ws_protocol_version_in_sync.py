"""Unit tests for ``scripts/check_ws_protocol_version_in_sync.py``.

Invokes the script as a subprocess from a temporary worktree that
overrides the backend + TypeScript source files, asserting the exit code
and stderr behaviour for matched / mismatched / missing-declaration
cases across every lockstep constant the gate checks.
"""

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "check_ws_protocol_version_in_sync.py"

# Sentinel distinguishing "write the standard in-sync body" (the default)
# from "skip this file entirely" (``None``).
_DEFAULT = object()


def _models_body(version: str = "3") -> str:
    return f"WS_PROTOCOL_VERSION: int = {version}\n"


def _ws_body(size: str = "32_768") -> str:
    return f"_MAX_OUTBOUND_EVENT_BYTES: int = {size}\n"


def _ts_body(version: str = "3", size: str = "32_768") -> str:
    return (
        f"export const WS_PROTOCOL_VERSION = {version}\n"
        f"export const WS_MAX_MESSAGE_SIZE = {size}\n"
    )


def _write_tree(
    tmp_root: Path,
    *,
    models: str | None | object = _DEFAULT,
    ws: str | None | object = _DEFAULT,
    ts: str | None | object = _DEFAULT,
) -> None:
    """Materialise the three source files the gate reads.

    Each argument is a file body, ``None`` to skip writing that file (the
    missing-file case), or left unset for the standard in-sync body.
    """
    resolved = {
        tmp_root / "src" / "synthorg" / "api" / "ws_models.py": (
            _models_body() if models is _DEFAULT else models
        ),
        tmp_root / "src" / "synthorg" / "api" / "controllers" / "ws.py": (
            _ws_body() if ws is _DEFAULT else ws
        ),
        tmp_root / "web" / "src" / "utils" / "ws-constants.ts": (
            _ts_body() if ts is _DEFAULT else ts
        ),
    }
    for path, body in resolved.items():
        if body is None:
            continue
        assert isinstance(body, str)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


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


@pytest.mark.unit
def test_passes_when_all_constants_match(tmp_path: Path) -> None:
    _write_tree(tmp_path)
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr


@pytest.mark.unit
def test_fails_on_version_mismatch(tmp_path: Path) -> None:
    _write_tree(tmp_path, models=_models_body("2"), ts=_ts_body(version="3"))
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "drift" in result.stderr.lower()
    assert "WS_PROTOCOL_VERSION" in result.stderr


@pytest.mark.unit
def test_fails_on_max_message_size_mismatch(tmp_path: Path) -> None:
    _write_tree(tmp_path, ws=_ws_body("16_384"), ts=_ts_body(size="32_768"))
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "drift" in result.stderr.lower()
    assert "WS_MAX_MESSAGE_SIZE" in result.stderr


@pytest.mark.unit
def test_underscored_literals_compare_equal(tmp_path: Path) -> None:
    # 32_768 (Python/TS underscore grouping) must equal a plain 32768.
    _write_tree(tmp_path, ws=_ws_body("32768"), ts=_ts_body(size="32_768"))
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr


@pytest.mark.unit
def test_fails_when_python_file_missing(tmp_path: Path) -> None:
    _write_tree(tmp_path, models=None)
    result = _run(tmp_path)
    assert result.returncode == 1


@pytest.mark.unit
def test_fails_when_version_declaration_missing(tmp_path: Path) -> None:
    _write_tree(tmp_path, models="# no version constant here\n")
    result = _run(tmp_path)
    assert result.returncode == 1


@pytest.mark.unit
def test_fails_when_size_declaration_missing(tmp_path: Path) -> None:
    _write_tree(tmp_path, ws="# no size constant here\n")
    result = _run(tmp_path)
    assert result.returncode == 1


@pytest.mark.integration
def test_repo_state_is_in_sync() -> None:
    """The committed Python and TypeScript constants must agree.

    Runs the gate script against the real repo source files, so this
    is an integration check rather than the file-level unit mark.
    """
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        cwd=str(_REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"WS lockstep constant drift detected: {result.stderr}"
    )
