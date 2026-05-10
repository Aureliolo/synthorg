"""Unit tests for ``scripts/check_no_em_dashes_hook.sh``.

The hook is a Bash PreToolUse intercept that fires before Edit/Write lands.
We invoke it as a subprocess feeding mock JSON envelopes on stdin and
assert on exit codes + stdout / stderr.

The em-dash patterns are constructed at runtime via ``chr(0x2014)`` and
string concatenation so this source file itself stays free of any
literal blocked pattern (otherwise the hook would block edits to this
test file -- the same trick is used in scripts/check_no_em_dashes.py).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "check_no_em_dashes_hook.sh"

_BASH = shutil.which("bash")
_BASH_AVAILABLE = pytest.mark.skipif(_BASH is None, reason="bash not available")

_EM_DASH = chr(0x2014)
_HTML_NAMED = "&" + "mdash;"
_HTML_DEC = "&" + "#8212;"
_HTML_HEX = "&" + "#x2014;"


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
def test_blocks_em_dash_in_write_content() -> None:
    result = _run(
        {
            "tool_input": {
                "file_path": "src/foo.py",
                "content": f"hello {_EM_DASH} world",
            },
        },
    )
    assert result.returncode == 2
    assert "permissionDecision" in result.stdout
    assert "deny" in result.stdout


@_BASH_AVAILABLE
def test_blocks_em_dash_in_edit_new_string() -> None:
    result = _run(
        {
            "tool_input": {
                "file_path": "src/foo.py",
                "old_string": "x",
                "new_string": f"hello {_EM_DASH} world",
            },
        },
    )
    assert result.returncode == 2


@_BASH_AVAILABLE
@pytest.mark.parametrize(
    "entity",
    [_HTML_NAMED, _HTML_DEC, _HTML_HEX],
)
def test_blocks_html_entities(entity: str) -> None:
    result = _run(
        {"tool_input": {"file_path": "src/foo.html", "content": f"foo {entity} bar"}},
    )
    assert result.returncode == 2


@_BASH_AVAILABLE
def test_allows_ascii_hyphen() -> None:
    result = _run(
        {"tool_input": {"file_path": "src/foo.py", "content": "hello - world"}},
    )
    assert result.returncode == 0


@_BASH_AVAILABLE
def test_allows_double_hyphen() -> None:
    result = _run(
        {"tool_input": {"file_path": "src/foo.py", "content": "foo -- bar"}},
    )
    assert result.returncode == 0


@_BASH_AVAILABLE
def test_skips_changelog_md() -> None:
    result = _run(
        {
            "tool_input": {
                "file_path": ".github/CHANGELOG.md",
                "content": f"hello {_EM_DASH} world",
            },
        },
    )
    assert result.returncode == 0


@_BASH_AVAILABLE
def test_skips_changelog_md_absolute_path() -> None:
    result = _run(
        {
            "tool_input": {
                "file_path": "/absolute/.github/CHANGELOG.md",
                "content": f"hello {_EM_DASH} world",
            },
        },
    )
    assert result.returncode == 0


@_BASH_AVAILABLE
def test_allows_empty_content() -> None:
    result = _run({"tool_input": {"file_path": "src/foo.py", "content": ""}})
    assert result.returncode == 0


@_BASH_AVAILABLE
def test_allows_when_no_file_path() -> None:
    result = _run({"tool_input": {}})
    assert result.returncode == 0
