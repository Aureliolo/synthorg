"""Unit tests for the ``scripts/run_edit_time_gates.py`` PostToolUse dispatcher."""

import importlib.util
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "run_edit_time_gates.py"


class _Gate(Protocol):
    """Structural view of the dispatcher's private routing entry."""

    script: str
    flag: str | None

    def applies_to(self, rel: str, suffix: str) -> bool: ...
    def argv(self, rel: str) -> list[str]: ...


class _ScriptModule(Protocol):
    """Subset of the dispatcher's surface the tests exercise."""

    _GATES: tuple[_Gate, ...]

    @staticmethod
    def _relative_path(raw: str) -> str | None: ...
    @staticmethod
    def main(argv: list[str] | None = None) -> int: ...


def _load() -> _ScriptModule:
    spec = importlib.util.spec_from_file_location("_run_edit_time_gates", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_ScriptModule, module)


dispatcher = _load()


def _exec(argv: list[str], stdin: str = "") -> subprocess.CompletedProcess[str]:
    """Run *argv* from the repo root, capturing both streams.

    The gates are exercised as real subprocesses rather than imported: the
    dispatcher's contract with them is the process boundary (argv in, exit
    code out), and an in-process call would not test the thing that breaks.
    """
    return subprocess.run(  # noqa: S603 -- fixed argv, no shell, no untrusted input
        argv,
        input=stdin,
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        check=False,
        timeout=120,
    )


def _gate_argv(script: str, *args: str) -> list[str]:
    """Return the argv for invoking gate *script* with *args*."""
    return [sys.executable, str(_REPO_ROOT / "scripts" / script), *args]


def _run(payload: object) -> subprocess.CompletedProcess[str]:
    """Invoke the dispatcher in hook mode with *payload* on stdin."""
    return _exec([sys.executable, str(_SCRIPT)], json.dumps(payload))


class TestRouting:
    """The routing table decides which gates could have an opinion."""

    def test_every_routed_gate_script_exists(self) -> None:
        for gate in dispatcher._GATES:
            assert (_REPO_ROOT / "scripts" / gate.script).is_file(), gate.script

    def test_every_routed_gate_accepts_its_scoping_flag(self) -> None:
        """A renamed flag must fail here, not silently scan the whole tree.

        Without this, dropping ``--files`` from a gate would make the
        dispatcher invoke it bare: the gate would scan everything, pass, and
        the hook would look like it was working.
        """
        for gate in dispatcher._GATES:
            if gate.flag is None:
                continue
            completed = _exec(_gate_argv(gate.script, "--help"))
            assert completed.returncode == 0, gate.script
            assert gate.flag in completed.stdout, f"{gate.script} lost {gate.flag}"

    @pytest.mark.parametrize(
        ("rel", "expected"),
        [
            ("src/synthorg/foo.py", True),
            ("tests/unit/test_foo.py", True),
            ("web/src/main.tsx", False),
            ("README.md", False),
            ("scripts/check_no_stubs.py", False),
            # A directory merely prefixed with a routed root must not match.
            ("src/synthorg_extra/foo.py", False),
        ],
    )
    def test_scope_prefix_matching(self, rel: str, expected: bool) -> None:
        suffix = Path(rel).suffix
        routed = any(gate.applies_to(rel, suffix) for gate in dispatcher._GATES)
        assert routed is expected

    def test_sql_routes_only_to_the_gate_that_reads_sql(self) -> None:
        rel = "src/synthorg/persistence/sqlite/schema.sql"
        routed = [g.script for g in dispatcher._GATES if g.applies_to(rel, ".sql")]
        assert routed == ["check_no_review_origin_in_code.py"]


class TestPathResolution:
    """Only a real file inside the repo can be routed."""

    def test_missing_file_is_not_resolved(self) -> None:
        assert dispatcher._relative_path("src/synthorg/does_not_exist.py") is None

    def test_outside_repo_is_not_resolved(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside.py"
        outside.write_text("x = 1\n", encoding="utf-8")
        assert dispatcher._relative_path(str(outside)) is None

    def test_absolute_in_repo_path_resolves_to_relative(self) -> None:
        absolute = _REPO_ROOT / "scripts" / "run_edit_time_gates.py"
        assert (
            dispatcher._relative_path(str(absolute)) == "scripts/run_edit_time_gates.py"
        )


class TestHookMode:
    """Malformed or out-of-scope payloads are a clean no-op, never a crash."""

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"tool_input": {}},
            {"tool_input": {"file_path": ""}},
            {"tool_input": "not-a-dict"},
            {"tool_input": {"file_path": "README.md"}},
            {"tool_input": {"file_path": "src/synthorg/gone.py"}},
        ],
    )
    def test_no_op_payloads_exit_zero(self, payload: object) -> None:
        completed = _run(payload)
        assert completed.returncode == 0, completed.stdout + completed.stderr

    def test_non_json_stdin_exits_zero(self) -> None:
        completed = _exec([sys.executable, str(_SCRIPT)], "not json at all")
        assert completed.returncode == 0

    def test_clean_in_scope_file_exits_zero(self) -> None:
        payload = {"tool_input": {"file_path": "src/synthorg/meta/mcp/server.py"}}
        completed = _run(payload)
        assert completed.returncode == 0, completed.stdout + completed.stderr


class TestViolationReporting:
    """A violating file is reported with the offending gate named."""

    def test_stub_and_frozen_model_violations_are_reported(self) -> None:
        probe = _REPO_ROOT / "src" / "synthorg" / "_edit_time_gate_test_probe.py"
        probe.write_text(
            '"""Probe for the edit-time dispatcher test."""\n'
            "\n"
            "from pydantic import BaseModel, ConfigDict\n"
            "\n"
            "\n"
            "class Probe(BaseModel):\n"
            '    """Missing extra=forbid and allow_inf_nan=False."""\n'
            "\n"
            "    model_config = ConfigDict(frozen=True)\n"
            "\n"
            "\n"
            "def unimplemented() -> None:\n"
            '    """A bare stub."""\n'
            "    raise NotImplementedError\n",
            encoding="utf-8",
        )
        try:
            completed = _run({"tool_input": {"file_path": str(probe)}})
        finally:
            probe.unlink()
        assert completed.returncode == 1
        assert "check_no_stubs.py" in completed.stdout
        assert "check_frozen_model_extra_forbid.py" in completed.stdout


class TestFileScopedGateModes:
    """``--files`` narrows scope without changing any gate's verdict shape."""

    @pytest.mark.parametrize(
        "script",
        [
            "check_no_stubs.py",
            "check_frozen_model_extra_forbid.py",
            "check_no_magic_numbers.py",
            "check_module_size_budget.py",
        ],
    )
    def test_out_of_scope_path_is_skipped_not_rejected(self, script: str) -> None:
        """A path no gate scopes to must pass, not error.

        The dispatcher hands over whatever was edited, so each gate owns the
        scope decision. Rejecting an out-of-scope path would turn every
        Markdown edit into a hook failure.
        """
        completed = _exec(_gate_argv(script, "--files", "README.md"))
        assert completed.returncode == 0, completed.stdout + completed.stderr

    @pytest.mark.parametrize(
        ("script", "update_flag"),
        [
            ("check_no_magic_numbers.py", "--update"),
            ("check_module_size_budget.py", "--update-baseline"),
        ],
    )
    def test_files_is_refused_with_a_baseline_update(
        self, script: str, update_flag: str
    ) -> None:
        """A baseline written from a partial scan would drop unvisited entries."""
        completed = _exec(
            _gate_argv(
                script, update_flag, "--files", "src/synthorg/meta/mcp/server.py"
            )
        )
        assert completed.returncode == 2
        assert "--files" in completed.stderr


class TestHookRegistration:
    """The dispatcher is wired in both harnesses, or it protects nothing."""

    def test_registered_in_claude_settings(self) -> None:
        settings = json.loads(
            (_REPO_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8")
        )
        commands = [
            hook["command"]
            for entry in settings["hooks"]["PostToolUse"]
            for hook in entry["hooks"]
        ]
        assert any("run_edit_time_gates.py" in cmd for cmd in commands)
        assert any("rewarm_mypy_after_sync.sh" in cmd for cmd in commands)

    def test_mirrored_in_opencode_plugin(self) -> None:
        plugin = (_REPO_ROOT / ".opencode" / "plugins" / "synthorg-hooks.ts").read_text(
            encoding="utf-8"
        )
        assert "run_edit_time_gates.py" in plugin
        assert "rewarm_mypy_after_sync.sh" in plugin

    def test_documented_in_the_gate_inventory(self) -> None:
        doc = (_REPO_ROOT / "docs" / "reference" / "convention-gates.md").read_text(
            encoding="utf-8"
        )
        assert "run_edit_time_gates.py" in doc
        assert "rewarm_mypy_after_sync.sh" in doc
        routed: Sequence[str] = [gate.script for gate in dispatcher._GATES]
        for script in routed:
            assert script in doc, f"{script} routed but absent from the inventory"
