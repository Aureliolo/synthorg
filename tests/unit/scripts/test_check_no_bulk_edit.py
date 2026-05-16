"""Unit tests for ``scripts/check_no_bulk_edit.py``.

Pins the policy contract: the native Edit tool (including
``replace_all: true``) and Write produce a reviewable atomic diff and
are NEVER blocked here; only shell in-place bulk-rewrite shortcuts
(``sed -i``, ``perl -pi``, redirect overwrite of a tracked source
file) are blocked because they bypass per-diff review.

Invoked as a subprocess fed a PreToolUse JSON envelope on stdin.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "check_no_bulk_edit.py"


def _run(envelope: dict[str, object]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(_SCRIPT)],
        input=json.dumps(envelope),
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )


def test_edit_replace_all_is_allowed() -> None:
    result = _run(
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "src/foo.py",
                "old_string": "x",
                "new_string": "y",
                "replace_all": True,
            },
        },
    )
    assert result.returncode == 0
    assert result.stderr == ""


def test_write_is_allowed() -> None:
    result = _run(
        {
            "tool_name": "Write",
            "tool_input": {
                "file_path": "src/foo.py",
                "content": "anything\n" * 500,
            },
        },
    )
    assert result.returncode == 0


@pytest.mark.parametrize(
    "command",
    [
        "sed -i 's/a/b/' src/foo.py",
        "gsed --in-place 's/a/b/' foo.py",
        "perl -pi -e 's/a/b/' foo.py",
        "perl -i.bak -pe 's/x/y/' a.py",
        "awk -i inplace '{print}' foo.py",
        "echo 'hello' > src/foo.py",
        "sed 's/a/b/' x.py >> tracked.md",
    ],
)
def test_shell_inplace_bulk_edits_blocked(command: str) -> None:
    result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
    assert result.returncode == 2, result.stdout
    assert "BLOCKED" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "sed 's/a/b/' foo.py",  # not in-place, stdout only
        "echo hello > /dev/null",
        "cat foo.py | grep x",
    ],
)
def test_benign_bash_allowed(command: str) -> None:
    result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
    assert result.returncode == 0, result.stderr


def test_empty_stdin_is_noop() -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(_SCRIPT)],
        input="",
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )
    assert result.returncode == 0


def test_whitespace_stdin_is_noop() -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(_SCRIPT)],
        input="  \n ",
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )
    assert result.returncode == 0


def test_malformed_json_fails_closed() -> None:
    # Present-but-unparseable stdin is an unknown state: the guard must
    # fail closed (exit 2) so a corrupted envelope cannot bypass it.
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(_SCRIPT)],
        input='{"tool_name": "Bash", ',
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )
    assert result.returncode == 2
    assert "malformed" in result.stderr.lower()
