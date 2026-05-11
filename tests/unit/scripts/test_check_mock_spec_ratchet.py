"""Tests for the mock-spec ratchet PreToolUse hook.

The hook reads a PreToolUse JSON envelope from stdin and either
allows the Edit / Write (exit 0) or blocks it (exit 2). Two
protections:

1. Edits to ``tests/*.py`` must not raise the gate's CATCH count.
2. Edits to ``scripts/check_mock_spec.py`` must not remove
   ``_Verdict.CATCH`` branches.
"""

import importlib.util
import io
import json
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol, cast

import pytest

pytestmark = pytest.mark.unit


class _RatchetModule(Protocol):
    """Subset of ``scripts/check_mock_spec_ratchet.py`` the tests touch."""

    _TESTS_ROOT: Path
    _GATE_PATH: Path

    @staticmethod
    def main() -> int: ...


def _load_module() -> _RatchetModule:
    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / "scripts" / "check_mock_spec_ratchet.py"
    spec = importlib.util.spec_from_file_location(
        "check_mock_spec_ratchet",
        script_path,
    )
    if spec is None or spec.loader is None:
        msg = f"could not load module spec for {script_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_RatchetModule, module)


_MODULE = _load_module()


def _envelope(tool_name: str, tool_input: dict[str, Any]) -> str:
    return json.dumps({"tool_name": tool_name, "tool_input": tool_input})


@pytest.fixture
def fake_tests_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Redirect the ratchet's ``_TESTS_ROOT`` to a tmp dir.

    The real repo's ``tests/`` tree is huge; we want isolated files
    we can craft for each scenario. `monkeypatch` reverts the
    attribute automatically at test teardown, so no explicit cleanup
    is needed.
    """
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    monkeypatch.setattr(_MODULE, "_TESTS_ROOT", tests_root)
    return tests_root


def _stub_stdin(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(raw))


def test_empty_stdin_allows(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_stdin(monkeypatch, "")
    assert _MODULE.main() == 0


def test_invalid_json_allows(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_stdin(monkeypatch, "{not valid json")
    assert _MODULE.main() == 0


def test_unsupported_tool_allows(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_stdin(monkeypatch, _envelope("Bash", {"command": "ls"}))
    assert _MODULE.main() == 0


def test_missing_file_path_allows(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_stdin(monkeypatch, _envelope("Edit", {"old_string": "a", "new_string": "b"}))
    assert _MODULE.main() == 0


def test_non_python_file_allows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "notes.md"
    target.write_text("hello", encoding="utf-8")
    _stub_stdin(
        monkeypatch,
        _envelope(
            "Edit",
            {
                "file_path": str(target),
                "old_string": "hello",
                "new_string": "world",
            },
        ),
    )
    assert _MODULE.main() == 0


@pytest.mark.usefixtures("fake_tests_root")
def test_file_outside_tests_and_not_gate_allows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "src" / "module.py"
    target.parent.mkdir()
    target.write_text("x = 1\n", encoding="utf-8")
    _stub_stdin(
        monkeypatch,
        _envelope(
            "Edit",
            {
                "file_path": str(target),
                "old_string": "x = 1",
                "new_string": "x = 2",
            },
        ),
    )
    assert _MODULE.main() == 0


def test_tests_shared_file_allows(
    monkeypatch: pytest.MonkeyPatch,
    fake_tests_root: Path,
) -> None:
    """Files under ``tests/_shared/`` are exempt; they host the helpers."""
    shared = fake_tests_root / "_shared"
    shared.mkdir()
    target = shared / "helper.py"
    target.write_text(
        "from unittest.mock import Mock\nclass Foo: ...\nFoo(Mock())\n",
        encoding="utf-8",
    )
    _stub_stdin(
        monkeypatch,
        _envelope(
            "Edit",
            {
                "file_path": str(target),
                "old_string": "Foo(Mock())",
                "new_string": "Foo(Mock(), Mock())",
            },
        ),
    )
    assert _MODULE.main() == 0


def test_no_op_edit_allows(
    monkeypatch: pytest.MonkeyPatch,
    fake_tests_root: Path,
) -> None:
    target = fake_tests_root / "test_thing.py"
    target.write_text("x = 1\n", encoding="utf-8")
    _stub_stdin(
        monkeypatch,
        _envelope(
            "Edit",
            {
                "file_path": str(target),
                "old_string": "x = 1",
                "new_string": "x = 1",
            },
        ),
    )
    assert _MODULE.main() == 0


def test_old_string_not_in_before_allows(
    monkeypatch: pytest.MonkeyPatch,
    fake_tests_root: Path,
) -> None:
    target = fake_tests_root / "test_thing.py"
    target.write_text("x = 1\n", encoding="utf-8")
    _stub_stdin(
        monkeypatch,
        _envelope(
            "Edit",
            {
                "file_path": str(target),
                "old_string": "y = 2",
                "new_string": "y = 3",
            },
        ),
    )
    assert _MODULE.main() == 0


_CATCH_BEFORE = "from unittest.mock import Mock\nclass Service: ...\nService(Mock())\n"


def test_test_file_edit_holding_count_allows(
    monkeypatch: pytest.MonkeyPatch,
    fake_tests_root: Path,
) -> None:
    target = fake_tests_root / "test_thing.py"
    target.write_text(_CATCH_BEFORE, encoding="utf-8")
    _stub_stdin(
        monkeypatch,
        _envelope(
            "Edit",
            {
                "file_path": str(target),
                "old_string": "Service(Mock())",
                "new_string": "Service(Mock(spec=Service))",
            },
        ),
    )
    assert _MODULE.main() == 0


def test_test_file_edit_raising_count_blocks(
    monkeypatch: pytest.MonkeyPatch,
    fake_tests_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = fake_tests_root / "test_thing.py"
    target.write_text(_CATCH_BEFORE, encoding="utf-8")
    _stub_stdin(
        monkeypatch,
        _envelope(
            "Edit",
            {
                "file_path": str(target),
                "old_string": "Service(Mock())",
                "new_string": "Service(Mock())\nService(Mock())",
            },
        ),
    )
    assert _MODULE.main() == 2
    captured = capsys.readouterr()
    assert "BLOCKED" in captured.err
    assert "ratchet" in captured.err


def test_test_file_edit_reducing_count_allows(
    monkeypatch: pytest.MonkeyPatch,
    fake_tests_root: Path,
) -> None:
    target = fake_tests_root / "test_thing.py"
    target.write_text(
        _CATCH_BEFORE + "Service(Mock())\n",
        encoding="utf-8",
    )
    _stub_stdin(
        monkeypatch,
        _envelope(
            "Edit",
            {
                "file_path": str(target),
                "old_string": "Service(Mock())\nService(Mock())\n",
                "new_string": (
                    "Service(Mock(spec=Service))\nService(Mock(spec=Service))\n"
                ),
            },
        ),
    )
    assert _MODULE.main() == 0


def test_write_tool_no_op_allows(
    monkeypatch: pytest.MonkeyPatch,
    fake_tests_root: Path,
) -> None:
    target = fake_tests_root / "test_thing.py"
    target.write_text(_CATCH_BEFORE, encoding="utf-8")
    _stub_stdin(
        monkeypatch,
        _envelope(
            "Write",
            {"file_path": str(target), "content": _CATCH_BEFORE},
        ),
    )
    assert _MODULE.main() == 0


def test_write_tool_raising_count_blocks(
    monkeypatch: pytest.MonkeyPatch,
    fake_tests_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = fake_tests_root / "test_thing.py"
    target.write_text(_CATCH_BEFORE, encoding="utf-8")
    new_content = _CATCH_BEFORE + "Service(Mock())\n"
    _stub_stdin(
        monkeypatch,
        _envelope("Write", {"file_path": str(target), "content": new_content}),
    )
    assert _MODULE.main() == 2
    assert "BLOCKED" in capsys.readouterr().err


@pytest.fixture
def fake_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Redirect the ratchet's ``_GATE_PATH`` to a stub gate file.

    The stub does NOT need to be importable; the gate-edit branch
    counts ``ast.Return`` nodes whose value is the
    ``_Verdict.CATCH`` attribute. Wrap each branch in a tiny function
    so the AST walker finds three matching Return nodes.
    """
    gate = tmp_path / "fake_gate.py"
    gate.write_text(
        "def _a(): return _Verdict.CATCH  # branch 1\n"
        "def _b(): return _Verdict.CATCH  # branch 2\n"
        "def _c(): return _Verdict.CATCH  # branch 3\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_MODULE, "_GATE_PATH", gate)
    return gate


def test_gate_edit_preserving_branches_allows(
    monkeypatch: pytest.MonkeyPatch,
    fake_gate: Path,
) -> None:
    _stub_stdin(
        monkeypatch,
        _envelope(
            "Edit",
            {
                "file_path": str(fake_gate),
                "old_string": "# branch 2",
                "new_string": "# branch 2 reworded",
            },
        ),
    )
    assert _MODULE.main() == 0


def test_gate_edit_removing_branch_blocks(
    monkeypatch: pytest.MonkeyPatch,
    fake_gate: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _stub_stdin(
        monkeypatch,
        _envelope(
            "Edit",
            {
                "file_path": str(fake_gate),
                "old_string": "def _b(): return _Verdict.CATCH  # branch 2\n",
                "new_string": "",
            },
        ),
    )
    assert _MODULE.main() == 2
    captured = capsys.readouterr()
    assert "BLOCKED" in captured.err
    assert "weakened" in captured.err


def test_gate_edit_adding_branch_allows(
    monkeypatch: pytest.MonkeyPatch,
    fake_gate: Path,
) -> None:
    _stub_stdin(
        monkeypatch,
        _envelope(
            "Edit",
            {
                "file_path": str(fake_gate),
                "old_string": "def _c(): return _Verdict.CATCH  # branch 3\n",
                "new_string": (
                    "def _c(): return _Verdict.CATCH  # branch 3\n"
                    "def _d(): return _Verdict.CATCH  # branch 4\n"
                ),
            },
        ),
    )
    assert _MODULE.main() == 0


def test_gate_edit_docstring_wording_change_allows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Editing a docstring that mentions ``_Verdict.CATCH`` must not block.

    Locks the AST-based counting in ``_count_catch_returns``: the
    naive substring counter blew the gate up when an innocuous
    wording change touched the literal string, and the new AST walk
    only counts real ``return _Verdict.CATCH`` statements.
    """
    gate = tmp_path / "fake_gate.py"
    before = (
        '"""Doc mentioning _Verdict.CATCH and _Verdict.CATCH again."""\n'
        "def _a(): return _Verdict.CATCH  # branch 1\n"
        "def _b(): return _Verdict.CATCH  # branch 2\n"
    )
    gate.write_text(before, encoding="utf-8")
    monkeypatch.setattr(_MODULE, "_GATE_PATH", gate)
    _stub_stdin(
        monkeypatch,
        _envelope(
            "Edit",
            {
                "file_path": str(gate),
                "old_string": "Doc mentioning _Verdict.CATCH and _Verdict.CATCH again.",
                "new_string": "Doc reworded with no marker references.",
            },
        ),
    )
    # Substring count would drop from 4 to 2 and block; AST count
    # stays at 2 both before and after.
    assert _MODULE.main() == 0


def test_gate_load_failure_falls_open(
    monkeypatch: pytest.MonkeyPatch,
    fake_tests_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A broken gate must not wedge editing; surface the cause to stderr."""

    def _boom() -> ModuleType:
        msg = "simulated gate-load failure"
        raise RuntimeError(msg)

    monkeypatch.setattr(_MODULE, "_load_gate", _boom)
    target = fake_tests_root / "test_thing.py"
    target.write_text(_CATCH_BEFORE, encoding="utf-8")
    _stub_stdin(
        monkeypatch,
        _envelope(
            "Edit",
            {
                "file_path": str(target),
                "old_string": "Service(Mock())",
                "new_string": "Service(Mock(spec=Service))",
            },
        ),
    )
    assert _MODULE.main() == 0
    captured = capsys.readouterr()
    assert "gate load failed" in captured.err
    assert "simulated gate-load failure" in captured.err
