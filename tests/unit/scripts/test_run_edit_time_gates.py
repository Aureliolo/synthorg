"""Unit tests for the ``scripts/run_edit_time_gates.py`` PostToolUse dispatcher."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "run_edit_time_gates.py"

# Comfortably inside the pytest-wide 30s ceiling, and just above the
# dispatcher's own 20s per-gate bound. A longer value would be worse than
# useless: ``timeout_method = "thread"`` cannot preempt a blocked OS-level wait
# (there is no signal path for that on Windows), so a subprocess timeout above
# the pytest one turns a should-be-fast failure into a silent multi-minute
# stall with no named failing node until the very end.
_SUBPROCESS_TIMEOUT_SECONDS = 25


class _Gate(Protocol):
    """Structural view of the dispatcher's private routing entry."""

    script: str
    flag: str | None
    suffixes: frozenset[str]
    roots: frozenset[str]

    def applies_to(self, rel: str, suffix: str) -> bool: ...
    def argv(self, rel: str) -> list[str]: ...


class _ScriptModule(Protocol):
    """Subset of the dispatcher's surface the tests exercise."""

    _GATES: tuple[_Gate, ...]
    _REPO_ROOT: Path

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
        encoding="utf-8",
        errors="replace",
        cwd=_REPO_ROOT,
        check=False,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    )


def _gate_argv(script: str, *args: str) -> list[str]:
    """Return the argv for invoking gate *script* with *args*."""
    return [sys.executable, str(_REPO_ROOT / "scripts" / script), *args]


def _run(payload: object) -> subprocess.CompletedProcess[str]:
    """Invoke the dispatcher in hook mode with *payload* on stdin."""
    return _exec([sys.executable, str(_SCRIPT)], json.dumps(payload))


def _sandbox(tmp_path: Path, rel: str, source: str) -> Path:
    """Write *source* at *rel* inside a fake repo root and return that root.

    The dispatcher resolves paths against its module-level ``_REPO_ROOT`` and
    invokes gates with ``cwd`` set to it, so pointing that at a tmp tree is what
    lets the real dispatcher-to-real-subprocess path be exercised without
    writing into the tracked working tree. Doing that would be the only test in
    this directory to mutate the real tree, and ``--dist=loadfile`` fences only
    pytest's own scheduling: a coverage collector, the mypy daemon, or a
    hard-killed run would all still observe the file.

    The gate scripts keep resolving to the real ``scripts/`` directory, since
    the dispatcher builds their argv from ``_SCRIPTS_DIR`` rather than from
    ``_REPO_ROOT``: the tree being scanned and the tree the gates live in are
    separate concerns, and only the former is redirected here.

    The root is resolved because the dispatcher compares a resolved candidate
    against it; ``tmp_path`` is not resolved by pytest, so on a platform whose
    temp directory is reached through a symlink (macOS ``/var``, a Windows
    junction) an unresolved root would fail containment for a reason that has
    nothing to do with the dispatcher.
    """
    root = tmp_path.resolve()
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")
    return root


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

    @pytest.mark.parametrize("gate_index", range(len(dispatcher._GATES)))
    def test_each_gate_matches_a_file_under_each_of_its_own_roots(
        self, gate_index: int
    ) -> None:
        """Per-gate, so one silently-dead gate cannot hide behind a sibling.

        An aggregate ``any(...)`` over the whole registry stays green when a
        single gate's roots are malformed, because another gate covering the
        same root still matches. This asserts each gate individually.
        """
        gate = dispatcher._GATES[gate_index]
        suffix = next(iter(sorted(gate.suffixes)))
        for root in gate.roots:
            assert gate.applies_to(f"{root}/probe{suffix}", suffix), (
                f"{gate.script} does not match its own root {root}"
            )

    def test_sql_routes_only_to_the_gate_that_reads_sql(self) -> None:
        rel = "src/synthorg/persistence/sqlite/schema.sql"
        routed = [g.script for g in dispatcher._GATES if g.applies_to(rel, ".sql")]
        assert routed == ["check_no_review_origin_in_code.py"]


class TestGateEntryValidation:
    """A malformed routing entry must fail at import, not go silently inert."""

    @pytest.mark.parametrize(
        "override",
        [
            {"roots": frozenset({"src/synthorg/"})},
            {"roots": frozenset({"/src/synthorg"})},
            {"roots": frozenset({"src\\synthorg"})},
            {"roots": frozenset()},
            {"suffixes": frozenset()},
            {"script": "sub/dir.py"},
            {"script": ""},
        ],
    )
    def test_malformed_entry_is_rejected(self, override: dict[str, object]) -> None:
        kwargs: dict[str, object] = {
            "script": "check_no_stubs.py",
            "flag": "--files",
            "suffixes": frozenset({".py"}),
            "roots": frozenset({"src/synthorg"}),
        }
        kwargs.update(override)
        gate_cls = type(dispatcher._GATES[0])
        with pytest.raises(ValueError, match=r"root|script|suffixes"):
            gate_cls(**kwargs)

    def test_registry_has_no_duplicate_script(self) -> None:
        scripts = [gate.script for gate in dispatcher._GATES]
        assert len(scripts) == len(set(scripts))


class TestScopeParity:
    """The dispatcher's roots must not drift from each gate's own scan root."""

    def test_dispatcher_roots_match_gate_scan_roots(self) -> None:
        """Two hand-maintained scope tables, pinned together.

        Drift in either direction silently narrows edit-time coverage: a gate
        whose own root grows stops being routed for the new subtree, and a
        dispatcher root the gate does not share means the gate is invoked and
        drops the path. Neither shows up as a failure anywhere else.
        """
        expected = {
            "check_no_stubs.py": {"src/synthorg"},
            "check_frozen_model_extra_forbid.py": {"src/synthorg", "tests"},
            "check_no_magic_numbers.py": {"src/synthorg"},
            "check_module_size_budget.py": {"src/synthorg"},
            "check_no_review_origin_in_code.py": {"src/synthorg", "tests"},
        }
        actual = {gate.script: set(gate.roots) for gate in dispatcher._GATES}
        assert actual == expected

    @pytest.mark.parametrize(
        ("script", "constant_pattern"),
        [
            ("check_no_stubs.py", 'SCAN_ROOT: Final = Path("src/synthorg")'),
            ("check_module_size_budget.py", '_SCAN_REL = Path("src") / "synthorg"'),
            ("check_no_magic_numbers.py", 'default=["src/synthorg"]'),
        ],
    )
    def test_gate_still_declares_the_scan_root_the_dispatcher_assumes(
        self, script: str, constant_pattern: str
    ) -> None:
        """Fail loudly if a gate's own scan-root declaration is edited.

        The parity test above compares the dispatcher against a literal; this
        one anchors that literal to the gate source, so widening a gate's scope
        cannot leave the pair agreeing with each other but wrong.
        """
        source = (_REPO_ROOT / "scripts" / script).read_text(encoding="utf-8")
        assert constant_pattern in source, (
            f"{script} no longer declares {constant_pattern!r}; "
            "update run_edit_time_gates.py's _GATES to match"
        )


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

    @pytest.mark.parametrize(
        "raw",
        [
            r"scripts\run_edit_time_gates.py",
            "scripts/run_edit_time_gates.py",
            r".\scripts\run_edit_time_gates.py",
        ],
    )
    def test_backslash_paths_normalise_to_posix(self, raw: str) -> None:
        """A native Windows path from the hook payload must still route.

        The routing table's roots are POSIX, so a backslash path that reached
        ``applies_to`` unnormalised would match nothing and the hook would fail
        open. CI is Linux-only, so nothing else pins this.
        """
        resolved = dispatcher._relative_path(raw)
        assert resolved == "scripts/run_edit_time_gates.py"

    def test_unresolvable_path_returns_none_rather_than_raising(self) -> None:
        """An embedded NUL must be a clean no-op, matching the sibling audits."""
        assert dispatcher._relative_path("src/synthorg/bad\x00name.py") is None


class TestHookMode:
    """Malformed payloads are a no-op, but never a silent one."""

    @pytest.mark.parametrize(
        "payload",
        [
            {"tool_input": {"file_path": "README.md"}},
            {"tool_input": {"file_path": "src/synthorg/gone.py"}},
        ],
    )
    def test_out_of_scope_payloads_exit_zero_quietly(self, payload: object) -> None:
        """A real path no gate wants is an ordinary no-op, not an anomaly."""
        completed = _run(payload)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert "WARNING" not in completed.stderr

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"tool_input": {}},
            {"tool_input": {"file_path": ""}},
            {"tool_input": "not-a-dict"},
            [],
        ],
    )
    def test_malformed_payloads_exit_zero_but_warn(self, payload: object) -> None:
        """Silence here would read as 'checked and clean' to both harnesses."""
        completed = _run(payload)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert "WARNING: run_edit_time_gates" in completed.stderr

    def test_non_json_stdin_warns(self) -> None:
        completed = _exec([sys.executable, str(_SCRIPT)], "not json at all")
        assert completed.returncode == 0
        assert "could not parse hook JSON" in completed.stderr

    def test_help_is_available_for_the_documented_cli_mode(self) -> None:
        """``--help`` must not be swallowed as a file path and exit 0 clean."""
        completed = _exec([sys.executable, str(_SCRIPT), "--help"])
        assert completed.returncode == 0
        assert "file_path" in completed.stdout


class TestViolationReporting:
    """A violating file is reported, tagged, and attributed to the right gate."""

    def test_stub_and_frozen_model_violations_are_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _sandbox(
            tmp_path,
            "src/synthorg/probe.py",
            '"""Probe."""\n'
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
        )
        monkeypatch.setattr(dispatcher, "_REPO_ROOT", root)
        monkeypatch.chdir(root)
        assert dispatcher.main(["src/synthorg/probe.py"]) == 1

    def test_positional_gate_gets_a_bare_path_with_no_flag(self) -> None:
        """Covers the argv shape of the one routed gate that takes no flag.

        ``test_every_routed_gate_accepts_its_scoping_flag`` skips a
        ``flag=None`` gate by construction, so nothing else pins this shape.
        Asserted at the argv level rather than end to end because
        ``check_no_review_origin_in_code`` resolves paths against its own
        module-level repo root with no override flag, so it cannot be pointed
        at a sandbox tree; detection is covered by that gate's own test file.
        """
        gate = next(g for g in dispatcher._GATES if g.flag is None)
        argv = gate.argv("src/synthorg/probe.py")
        assert argv[-1] == "src/synthorg/probe.py"
        assert not any(arg.startswith("--") for arg in argv)
        assert argv[1].endswith(gate.script)

    def test_clean_sandboxed_file_exits_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A purpose-built clean file, not a live production module.

        Pointing this at a real module would couple the dispatcher's test to
        that module's future size and content.
        """
        root = _sandbox(
            tmp_path,
            "src/synthorg/probe.py",
            '"""Probe."""\n\nVALUE = 1\n',
        )
        monkeypatch.setattr(dispatcher, "_REPO_ROOT", root)
        monkeypatch.chdir(root)
        assert dispatcher.main(["src/synthorg/probe.py"]) == 0


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
        "script",
        [
            "check_no_stubs.py",
            "check_frozen_model_extra_forbid.py",
            "check_no_magic_numbers.py",
            "check_module_size_budget.py",
        ],
    )
    def test_wholly_out_of_scope_input_says_so(self, script: str) -> None:
        """Silence would be indistinguishable from a clean scan.

        This is the signal that a drifted routing table is visible rather than
        quietly narrowing coverage.
        """
        completed = _exec(_gate_argv(script, "--files", "README.md"))
        assert "nothing" in completed.stderr.lower(), completed.stderr

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

    def test_magic_numbers_files_mode_reports_a_violation(self, tmp_path: Path) -> None:
        """Proves the narrowed path actually detects, not merely selects.

        A module-level assignment of a plain numeric constant, one of the two
        shapes this gate flags (the other is a function default). The value has
        to be an ``ast.Constant``: a ``BinOp`` such as ``7919 * 31`` is
        deliberately not a violation, and a literal inside a function body is
        outside this gate's detection entirely.
        """
        probe = tmp_path / "src" / "synthorg" / "probe.py"
        probe.parent.mkdir(parents=True)
        probe.write_text('"""Probe."""\n\nVALUE = 7919\n', encoding="utf-8")
        completed = _exec(
            _gate_argv(
                "check_no_magic_numbers.py",
                "--repo-root",
                str(tmp_path),
                "--files",
                "src/synthorg/probe.py",
            )
        )
        assert completed.returncode == 1, completed.stdout + completed.stderr
        assert "probe.py" in completed.stdout

    def test_module_size_files_mode_reports_a_violation(self, tmp_path: Path) -> None:
        """Proves the narrowed path actually detects, not merely selects."""
        probe = tmp_path / "src" / "synthorg" / "controllers" / "probe.py"
        probe.parent.mkdir(parents=True)
        body = '"""Probe."""\n\n' + "".join(
            f"VALUE_{index} = {index}\n" for index in range(2000)
        )
        probe.write_text(body, encoding="utf-8")
        baseline = tmp_path / "baseline.json"
        baseline.write_text('{"locations": {}}', encoding="utf-8")
        completed = _exec(
            _gate_argv(
                "check_module_size_budget.py",
                "--project-root",
                str(tmp_path),
                "--baseline",
                str(baseline),
                "--files",
                "src/synthorg/controllers/probe.py",
            )
        )
        assert completed.returncode == 1, completed.stdout + completed.stderr
        assert "probe.py" in completed.stderr


class TestHookRegistration:
    """The dispatcher is wired in both harnesses, on the right matcher."""

    @staticmethod
    def _post_tool_use_commands(matcher: str) -> list[str]:
        settings = json.loads(
            (_REPO_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8")
        )
        return [
            hook["command"]
            for entry in settings["hooks"]["PostToolUse"]
            if entry.get("matcher") == matcher
            for hook in entry["hooks"]
        ]

    def test_dispatcher_registered_on_the_edit_write_matcher(self) -> None:
        """Matcher-scoped: on the Bash matcher it would never see a file edit."""
        commands = self._post_tool_use_commands("Edit|Write")
        assert any("run_edit_time_gates.py" in cmd for cmd in commands)

    def test_rewarm_registered_on_the_bash_matcher(self) -> None:
        """Matcher-scoped: on Edit|Write it would never see a `uv sync`."""
        commands = self._post_tool_use_commands("Bash")
        assert any("rewarm_caches_after_sync.sh" in cmd for cmd in commands)

    def test_mirrored_in_opencode_plugin(self) -> None:
        plugin = (_REPO_ROOT / ".opencode" / "plugins" / "synthorg-hooks.ts").read_text(
            encoding="utf-8"
        )
        assert "run_edit_time_gates.py" in plugin
        assert "rewarm_caches_after_sync.sh" in plugin

    def test_documented_in_the_dispatchers_own_doc_section(self) -> None:
        """Scoped to the dispatcher's section, not the whole document.

        Every routed gate also has its own unrelated row in the gate-inventory
        table, so a whole-document substring search would stay green even if
        the dispatcher's routing table were deleted outright.
        """
        doc = (_REPO_ROOT / "docs" / "reference" / "convention-gates.md").read_text(
            encoding="utf-8"
        )
        start = doc.index("### Edit-time gate dispatcher")
        section = doc[start : doc.index("### Post-sync cache re-warm", start)]
        for gate in dispatcher._GATES:
            assert gate.script in section, (
                f"{gate.script} is routed but absent from the dispatcher's "
                "own documented table"
            )
