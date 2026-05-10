"""Unit tests for ``scripts/check_no_edit_baseline.sh``.

The hook is a Bash PreToolUse intercept that fires before Edit/Write lands
on a protected baseline file. We invoke it as a subprocess feeding mock
JSON envelopes on stdin and assert on exit codes + stdout / stderr.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "check_no_edit_baseline.sh"

_BASH = shutil.which("bash")
_BASH_AVAILABLE = pytest.mark.skipif(_BASH is None, reason="bash not available")


def _run(envelope: dict[str, object]) -> subprocess.CompletedProcess[str]:
    assert _BASH is not None
    return subprocess.run(  # noqa: S603
        [_BASH, str(_SCRIPT)],
        input=json.dumps(envelope),
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )


@_BASH_AVAILABLE
def test_blocks_edit_of_text_baseline_posix() -> None:
    result = _run(
        {
            "tool_input": {
                "file_path": "scripts/mock_spec_baseline.txt",
                "new_string": "src/x.py:1:1\n",
            },
        },
    )
    assert result.returncode == 2


@_BASH_AVAILABLE
def test_blocks_edit_of_json_baseline_posix() -> None:
    result = _run(
        {
            "tool_input": {
                "file_path": "scripts/_workflow_shell_git_commits_baseline.json",
                "new_string": "{}",
            },
        },
    )
    assert result.returncode == 2


@_BASH_AVAILABLE
def test_blocks_edit_of_test_timing_baseline_posix() -> None:
    result = _run(
        {
            "tool_input": {
                "file_path": "tests/baselines/unit_timing.json",
                "new_string": "{}",
            },
        },
    )
    assert result.returncode == 2


@_BASH_AVAILABLE
def test_allows_non_baseline_path() -> None:
    result = _run(
        {
            "tool_input": {
                "file_path": "src/synthorg/foo.py",
                "new_string": "x = 1",
            },
        },
    )
    assert result.returncode == 0


@_BASH_AVAILABLE
@pytest.mark.parametrize(
    "windows_path",
    [
        r"C:\repo\scripts\mock_spec_baseline.txt",
        r"scripts\mock_spec_baseline.txt",
        r"D:\projects\synthorg\scripts\_workflow_shell_git_commits_baseline.json",
        r"C:\repo\tests\baselines\unit_timing.json",
    ],
)
def test_blocks_edit_of_baseline_via_windows_path(windows_path: str) -> None:
    """Windows-style backslash paths must hit the same protection as POSIX.

    Without normalisation, the case patterns only match ``/``-separated paths
    and a Windows path like ``C:\\repo\\scripts\\mock_spec_baseline.txt``
    would slip past the gate. The hook normalises backslashes before the
    case match.
    """
    result = _run(
        {
            "tool_input": {
                "file_path": windows_path,
                "new_string": "src/x.py:1:1\n",
            },
        },
    )
    assert result.returncode == 2


@_BASH_AVAILABLE
def test_allows_when_no_file_path() -> None:
    result = _run({"tool_input": {}})
    assert result.returncode == 0


@_BASH_AVAILABLE
def test_allows_malformed_json_envelope() -> None:
    """Malformed stdin must not crash the hook; falls through to allow.

    Choosing fail-closed (block) here would brick Edit/Write across the
    entire session if jq itself misbehaved or the harness sent a non-JSON
    envelope. Allowing through is the deliberate posture: the in-repo
    pre-commit baseline-growth gate is the diff-time backstop.
    """
    assert _BASH is not None
    result = subprocess.run(  # noqa: S603
        [_BASH, str(_SCRIPT)],
        input="this is not json at all { ",
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )
    assert result.returncode == 0
