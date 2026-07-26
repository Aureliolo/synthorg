"""Tests for the concurrent pre-push hook-group runner.

The runner exists to keep a push inside its five-minute budget, so the
behaviour that matters is: the tools genuinely overlap, every tool runs
(no short-circuit on the first failure), every failure propagates, and a
file-taking tool receives only the paths it can actually process, since
the group's pre-commit filter is the union of its tools' interests.
"""

import importlib.util
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]

# A concurrency proof must fail loudly rather than wedge a worker: a
# sequential runner never assembles the party, so the rendezvous has to
# give up on its own.
_BARRIER_TIMEOUT_SECONDS = 10.0


class _ToolShape(Protocol):
    """The fields the tests read off a declared tool."""

    name: str
    argv: tuple[str, ...]
    filename_pattern: re.Pattern[str] | None
    whole_scope: tuple[str, ...]


class _ToolFactory(Protocol):
    """The runner's ``_Tool`` constructor, as the tests call it."""

    def __call__(
        self,
        name: str,
        argv: tuple[str, ...],
        *,
        whole_scope: tuple[str, ...] = ...,
    ) -> _ToolShape: ...


class _GateModule(Protocol):
    """Subset of ``scripts/run_prepush_hook_group.py`` the tests exercise.

    The declarations are the runner's own private names; the tests reach
    for them because the registry's validity is part of the contract, not
    an implementation detail: a malformed group would exit 0 having run
    nothing.
    """

    _Tool: _ToolFactory
    _GROUPS: Mapping[str, tuple[_ToolShape, ...]]
    _validate_groups: Callable[[Mapping[str, tuple[object, ...]]], None]
    _TOOL_TIMEOUT_SECONDS: int

    @staticmethod
    def main() -> int: ...


def _load() -> _GateModule:
    script_path = _REPO_ROOT / "scripts" / "run_prepush_hook_group.py"
    spec = importlib.util.spec_from_file_location("run_prepush_hook_group", script_path)
    if spec is None or spec.loader is None:
        msg = f"could not load module spec for {script_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_GateModule, module)


_MODULE = _load()


@dataclass
class _FakeProcess:
    """Stand-in for the ``Popen`` the runner now opens per tool.

    The runner moved off ``subprocess.run(timeout=...)`` because run's
    timeout path kills only the direct child and then drains the pipes
    without bound, so a surviving grandchild hangs the push. Modelling
    ``communicate`` (and its ``TimeoutExpired``) is therefore the only way
    to exercise the code path that used to hang.
    """

    argv: list[str]
    returncode: int
    wedge: bool = False
    drain_also_wedges: bool = False
    pid: int = 4242
    killed: bool = False
    communicate_calls: int = 0

    def __enter__(self) -> _FakeProcess:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        """Return the tool's output, or refuse to finish when wedged.

        Returns:
            The captured ``(stdout, stderr)`` pair.

        Raises:
            subprocess.TimeoutExpired: When this tool is meant to wedge.
        """
        self.communicate_calls += 1
        first_call = self.communicate_calls == 1
        if self.wedge and (first_call or self.drain_also_wedges):
            raise subprocess.TimeoutExpired(self.argv, timeout or 0.0)
        return ("out", "err")

    def kill(self) -> None:
        self.killed = True


def _names(argv: Sequence[str], token: str) -> bool:
    """Whether *argv* invokes the tool *token* identifies.

    Substring rather than element equality: a tool reached through its entry
    point (``node .../eslint/bin/eslint.js``) carries its name inside a path
    rather than as an argument of its own, and a test naming the tool should
    not have to track which invocation form the runner currently uses.

    Returns:
        ``True`` when any argument contains the token.
    """
    return any(token in arg for arg in argv)


@dataclass
class _Recorder:
    """Captures every subprocess the runner launches.

    ``returncodes`` maps a token appearing in a tool's argv to the exit
    code that tool should report. A single shared code cannot express the
    case the runner is built around -- one tool failing while its siblings
    pass -- so the map is keyed per tool.
    """

    calls: list[list[str]] = field(default_factory=list)
    kwargs: list[Mapping[str, object]] = field(default_factory=list)
    returncodes: Mapping[str, int] = field(default_factory=dict)
    # Tokens naming a tool that should never return, standing in for the real
    # wedge this runner exists to survive: a Node process that stops making
    # progress while still holding the captured pipe open.
    wedged: frozenset[str] = field(default_factory=frozenset)
    # When set, the post-kill drain wedges too, i.e. the tree kill failed to
    # close the pipes. The runner must still return rather than trading one
    # unbounded wait for another.
    drain_also_wedges: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __call__(self, argv: list[str], **kwargs: object) -> _FakeProcess:
        with self.lock:
            self.calls.append(list(argv))
            self.kwargs.append(dict(kwargs))
        returncode = next(
            (code for token, code in self.returncodes.items() if _names(argv, token)), 0
        )
        return _FakeProcess(
            argv=list(argv),
            returncode=returncode,
            wedge=any(_names(argv, token) for token in self.wedged),
            drain_also_wedges=self.drain_also_wedges,
        )

    def argv_for(self, token: str) -> list[str]:
        """Return the argv of the single call naming *token*.

        Raises:
            AssertionError: When no call, or more than one, matches.
        """
        matches = [call for call in self.calls if _names(call, token)]
        assert len(matches) == 1, f"expected one {token!r} call, got {len(matches)}"
        return matches[0]

    def ran(self, token: str) -> bool:
        """Whether any launched command named *token*.

        Returns:
            ``True`` when at least one call's argv carries the token.
        """
        return any(_names(call, token) for call in self.calls)


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    recorder: _Recorder,
    kills: list[list[str]] | None = None,
) -> None:
    monkeypatch.setattr(subprocess, "Popen", recorder)
    # ``_terminate_tree`` shells out to ``taskkill`` on Windows, which is the
    # only remaining ``subprocess.run`` in the runner. Route it somewhere the
    # test can inspect instead of letting a real taskkill run against a fake
    # pid, which would either fail or, worse, hit an unrelated process.
    kill_log = kills if kills is not None else []

    def _fake_run(argv: list[str], **_kwargs: object) -> object:
        kill_log.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    # ``shutil.which`` resolves npm to npm.cmd on Windows; the tests care
    # about the argument list, not the resolved interpreter path. The
    # runner imported this same module object, so patching it here reaches
    # the call site.
    monkeypatch.setattr(shutil, "which", lambda name: name)


def _run(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    recorder: _Recorder,
    kills: list[list[str]] | None = None,
) -> int:
    _patch(monkeypatch, recorder, kills)
    monkeypatch.setattr("sys.argv", ["run_prepush_hook_group.py", *argv])
    return _MODULE.main()


class TestGroupDeclarations:
    """The registry is validated at import time, so a typo never ships."""

    @pytest.mark.parametrize(
        ("name", "argv"),
        [
            pytest.param("", ("uv", "run", "vulture"), id="blank_name"),
            pytest.param("   ", ("uv", "run", "vulture"), id="whitespace_name"),
            pytest.param("vulture", (), id="empty_argv"),
        ],
    )
    def test_a_malformed_tool_is_rejected(
        self, name: str, argv: tuple[str, ...]
    ) -> None:
        with pytest.raises(ValueError, match="must not be"):
            _MODULE._Tool(name, argv)

    def test_an_empty_group_is_rejected(self) -> None:
        # A group with no tools would exit 0 having verified nothing.
        with pytest.raises(ValueError, match="declares no tools"):
            _MODULE._validate_groups({"empty": ()})

    def test_a_duplicate_tool_name_is_rejected(self) -> None:
        # Two tools sharing a name collapse into one line of the report,
        # hiding whichever ran second.
        tool = _MODULE._Tool("vulture", ("uv", "run", "vulture"))
        with pytest.raises(ValueError, match="repeats a tool name"):
            _MODULE._validate_groups({"dupe": (tool, tool)})

    def test_every_declared_group_is_well_formed(self) -> None:
        _MODULE._validate_groups(_MODULE._GROUPS)

    def test_eslint_skips_ignored_files_instead_of_failing(self) -> None:
        # A changed set can include generated ``*.gen.ts`` files that eslint's
        # config ignores. Without ``--no-warn-ignored`` eslint emits a "File
        # ignored" warning for each, which ``--max-warnings 0`` turns into a
        # push-blocking failure on files no one was ever meant to lint.
        eslint = next(
            tool for tool in _MODULE._GROUPS["web-checks"] if tool.name == "eslint"
        )
        assert "--no-warn-ignored" in eslint.argv

    def test_eslint_does_not_route_its_path_list_through_a_shell(self) -> None:
        # ``npm`` is ``npm.cmd`` on Windows, so invoking eslint through it puts
        # cmd.exe between the runner and the tool -- and cmd.exe caps a command
        # line at 8191 characters, which a hundred-odd web paths clear. Going
        # straight to the entry point keeps the ceiling at CreateProcess's.
        eslint = next(
            tool for tool in _MODULE._GROUPS["web-checks"] if tool.name == "eslint"
        )
        assert eslint.argv[0] == "node"
        assert "npm" not in eslint.argv

    def test_a_blank_whole_scope_path_is_rejected(self) -> None:
        # An empty path would reach the tool as the working directory and lint
        # the entire repository, which reads as a very slow pass, not a defect.
        with pytest.raises(ValueError, match="whole_scope"):
            _MODULE._Tool("eslint", ("node", "x.js"), whole_scope=("web/src", " "))

    def test_a_file_taking_tool_declares_a_whole_scope_to_fall_back_to(self) -> None:
        # Without one, an over-long path list has nowhere to go but truncation.
        for tool in _MODULE._GROUPS["web-checks"]:
            if tool.filename_pattern is not None:
                assert tool.whole_scope


class TestGroupDispatch:
    def test_unknown_group_is_a_config_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _Recorder()
        assert _run(monkeypatch, ["not-a-group"], recorder) == 2
        assert recorder.calls == []

    @pytest.mark.parametrize(
        ("group", "expected"),
        [
            pytest.param(
                "python-audits", ("vulture", "interrogate", "deptry"), id="python"
            ),
            pytest.param("web-checks", ("lint:knip", "lint:circular"), id="web"),
        ],
    )
    def test_every_tool_in_the_group_runs(
        self,
        monkeypatch: pytest.MonkeyPatch,
        group: str,
        expected: tuple[str, ...],
    ) -> None:
        recorder = _Recorder()
        assert _run(monkeypatch, [group], recorder) == 0
        for token in expected:
            assert recorder.ran(token), f"{token} never ran"

    def test_a_failing_tool_fails_the_group(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _Recorder(returncodes={"vulture": 1})
        assert _run(monkeypatch, ["python-audits"], recorder) == 1

    def test_one_failure_does_not_suppress_a_passing_sibling(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # One push should surface every failure, not just the first: a
        # developer fixing them one per push is exactly the loop this
        # runner exists to shorten. A single shared exit code cannot prove
        # that, so each tool reports its own.
        recorder = _Recorder(returncodes={"vulture": 1, "deptry": 3})
        assert _run(monkeypatch, ["python-audits"], recorder) == 1
        assert len(recorder.calls) == 3
        out = capsys.readouterr().out
        assert "vulture (exit 1)" in out
        assert "deptry (exit 3)" in out
        assert "interrogate (exit" not in out

    def test_a_tool_missing_from_path_fails_the_group(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # A tool that cannot start must read as a failure, never as a
        # silent pass: an unresolvable command means the check did not run.
        recorder = _Recorder()
        monkeypatch.setattr(subprocess, "run", recorder)
        monkeypatch.setattr(shutil, "which", lambda _name: None)
        monkeypatch.setattr("sys.argv", ["run_prepush_hook_group.py", "python-audits"])

        assert _MODULE.main() == 1
        assert recorder.calls == []
        assert "is not on PATH" in capsys.readouterr().out

    def test_a_tool_that_cannot_spawn_fails_the_group(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # An OSError escaping a worker would surface as a bare traceback
        # from ``pool.map`` and discard every sibling's result.
        def _explode(argv: list[str], **_kwargs: object) -> object:
            msg = "spawn refused"
            raise OSError(msg)

        monkeypatch.setattr(subprocess, "Popen", _explode)
        monkeypatch.setattr(shutil, "which", lambda name: name)
        monkeypatch.setattr("sys.argv", ["run_prepush_hook_group.py", "python-audits"])

        assert _MODULE.main() == 1
        assert "failed to start: OSError" in capsys.readouterr().out

    def test_a_wedged_tool_times_out_and_fails_the_group(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # This runner gates every push, so a hung tool must read as a
        # failure, never block the push with no exit but Ctrl-C. The wedge
        # is modelled on ``communicate`` rather than the spawn, because that
        # is where a real tool stalls and where the runner must intervene.
        recorder = _Recorder(wedged=frozenset({"vulture", "interrogate", "deptry"}))
        assert _run(monkeypatch, ["python-audits"], recorder) == 1
        assert "timed out" in capsys.readouterr().out

    def test_the_tools_actually_overlap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The whole point of the runner is wall-clock, not tidiness.

        A sequential implementation passes every other test in this file,
        so the concurrency is pinned directly: each tool waits at a
        rendezvous that only opens once all three have arrived.
        """
        barrier = threading.Barrier(len(_MODULE._GROUPS["python-audits"]))

        def _rendezvous(
            argv: list[str], **_kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(subprocess, "run", _rendezvous)
        monkeypatch.setattr(shutil, "which", lambda name: name)
        monkeypatch.setattr("sys.argv", ["run_prepush_hook_group.py", "python-audits"])

        assert _MODULE.main() == 0

    def test_the_timing_summary_names_every_tool(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Without a printed duration a scope regression is only ever felt.
        recorder = _Recorder()
        _run(monkeypatch, ["python-audits"], recorder)
        out = capsys.readouterr().out
        assert re.search(r"python-audits: .*\d+\.\d+s wall-clock", out)
        for name in ("vulture", "interrogate", "deptry"):
            assert re.search(rf"{name} \d+\.\d+s", out)


class TestSubprocessInvocation:
    def test_every_tool_runs_from_the_repository_root(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # pre-commit's cwd is the repo root today, but a tool resolving
        # ``pyproject.toml`` relative to somewhere else would silently
        # audit the wrong tree rather than fail.
        recorder = _Recorder()
        _run(monkeypatch, ["python-audits"], recorder)
        assert recorder.kwargs
        for call_kwargs in recorder.kwargs:
            assert call_kwargs["cwd"] == _REPO_ROOT
            # Both streams must be captured for the report to have anything
            # to print, and the runner must never raise on a non-zero exit:
            # it reports every tool's verdict, it does not abort on the first.
            assert call_kwargs["stdout"] is subprocess.PIPE
            assert call_kwargs["stderr"] is subprocess.PIPE

    def test_a_wedged_tool_is_killed_with_its_whole_tree(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The defect this guards: ``subprocess.run(timeout=...)`` kills only
        # the direct child, then drains the pipes unbounded. ``npm run`` is
        # ``node -> cmd.exe -> node``, so the surviving grandchild held the
        # captured stdout open and a 180s budget overran to 1034s of real
        # wall-clock, releasable only by killing the tree by hand.
        kills: list[list[str]] = []
        recorder = _Recorder(wedged=frozenset({"deptry"}))
        exit_code = _run(monkeypatch, ["python-audits"], recorder, kills=kills)

        assert exit_code == 1
        report = capsys.readouterr().out
        assert "timed out after" in report
        assert "killed with its process tree" in report
        # The report must not let a gate defect read as a lint finding.
        assert "GATE DEFECT" in report
        if sys.platform == "win32":
            assert any("taskkill" in call for call in kills), (
                f"expected a taskkill for the wedged tree, got {kills}"
            )
            assert any("/T" in call for call in kills), (
                "taskkill must carry /T or the grandchildren survive"
            )

    def test_the_post_kill_drain_cannot_hang_the_push(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Even when the tree kill fails to close the pipes, the runner must
        # give up on the output rather than wait: the whole point is that no
        # child can hold a push open, so the fix must not reintroduce the
        # unbounded wait one level down.
        recorder = _Recorder(wedged=frozenset({"deptry"}), drain_also_wedges=True)
        exit_code = _run(monkeypatch, ["python-audits"], recorder)

        assert exit_code == 1
        assert "timed out after" in capsys.readouterr().out

    def test_output_is_decoded_leniently(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A tool emitting a stray byte must not crash the group with a
        # UnicodeDecodeError in place of its own diagnostic.
        recorder = _Recorder()
        _run(monkeypatch, ["python-audits"], recorder)
        for call_kwargs in recorder.kwargs:
            assert call_kwargs["text"] is True
            assert call_kwargs["errors"] == "replace"


class TestHookDeclaration:
    def test_a_file_taking_group_hook_is_serial(self) -> None:
        # The runner already parallelises the tools inside a group. Without
        # ``require_serial``, pre-commit adds a second dimension on top: it
        # partitions the matched files and runs the hook once per chunk in
        # parallel, so a 22-file web push spawned six concurrent groups and
        # eighteen Node processes, which exhausted memory and killed tools
        # mid-parse.
        #
        # Only a hook that actually receives filenames can be chunked, so
        # ``pass_filenames: false`` is an equally valid way to be immune;
        # requiring the flag there would be cargo cult. Any future group hook
        # must satisfy one of the two.
        config = (_REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
        blocks = re.split(r"\n      - id: ", config)
        offenders = [
            block.split("\n", 1)[0]
            for block in blocks[1:]
            if "run_prepush_hook_group.py" in block
            and "require_serial: true" not in block
            and "pass_filenames: false" not in block
        ]
        assert not offenders, (
            "a hook-group entry that receives filenames must declare "
            "require_serial: true, or pass_filenames: false, so pre-commit "
            f"does not fan it out per file chunk: {offenders}"
        )


class TestFilenameRouting:
    @pytest.mark.parametrize(
        "lintable",
        [
            pytest.param("web/src/pages/Thing.tsx", id="src_tsx"),
            pytest.param("web/src/api/client.ts", id="src_ts"),
            pytest.param("web/test-infra/setup.ts", id="test_infra_ts"),
        ],
    )
    def test_file_taking_tool_receives_only_its_own_paths(
        self, monkeypatch: pytest.MonkeyPatch, lintable: str
    ) -> None:
        recorder = _Recorder()
        _run(
            monkeypatch,
            ["web-checks", lintable, "web/package.json", "web/src/styles.css"],
            recorder,
        )
        eslint = recorder.argv_for("eslint")
        assert eslint[-1] == lintable
        assert "web/package.json" not in eslint
        assert "web/src/styles.css" not in eslint

    def test_paths_reach_the_tool_behind_an_option_separator(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The hook config's own ``--`` is consumed by argparse, so unless the
        # runner re-emits one, a path beginning with a dash reaches the tool
        # as an option and the documented protection is fiction.
        recorder = _Recorder()
        _run(monkeypatch, ["web-checks", "web/src/api/client.ts"], recorder)
        assert recorder.argv_for("eslint")[-2:] == ["--", "web/src/api/client.ts"]

    @pytest.mark.parametrize(
        "unlintable",
        [
            pytest.param("web/package.json", id="manifest"),
            pytest.param("web/src/styles.css", id="stylesheet"),
            pytest.param("src/synthorg/core/agent.py", id="python"),
            pytest.param("web/vite.config.ts", id="ts_outside_the_linted_roots"),
        ],
    )
    def test_file_taking_tool_is_skipped_when_nothing_matches(
        self, monkeypatch: pytest.MonkeyPatch, unlintable: str
    ) -> None:
        # Invoking ESLint with no paths would make it fall back to its
        # configured scope, which is the whole-tree cost being avoided.
        recorder = _Recorder()
        _run(monkeypatch, ["web-checks", unlintable], recorder)
        assert not recorder.ran("eslint")

    def test_an_over_long_path_list_widens_to_the_whole_scope(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Windows refuses a command line past 32767 characters. Dropping the
        # excess would leave a gate reporting success on files it never read,
        # so the runner widens instead: the whole scope contains every path it
        # would otherwise have passed.
        many = [f"web/src/pages/Page{i:05d}Component.tsx" for i in range(2_000)]
        recorder = _Recorder()
        _run(monkeypatch, ["web-checks", *many], recorder)
        eslint = recorder.argv_for("eslint")
        assert eslint[-2:] == ["web/src", "web/test-infra"]
        assert not any(path in eslint for path in many)

    def test_a_path_list_that_fits_is_passed_file_by_file(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The widening above must stay exceptional: if it fired for an ordinary
        # push, every web change would pay a whole-scope lint and the runner's
        # reason for filtering at all would be gone.
        few = [f"web/src/pages/Page{i:05d}Component.tsx" for i in range(20)]
        recorder = _Recorder()
        _run(monkeypatch, ["web-checks", *few], recorder)
        eslint = recorder.argv_for("eslint")
        assert eslint[-len(few) :] == few
        assert "web/src" not in eslint

    def test_widening_is_reported_rather_than_silent(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # A gate that quietly changed what it inspected is the failure mode
        # this runner's timing output exists to expose.
        many = [f"web/src/pages/Page{i:05d}Component.tsx" for i in range(2_000)]
        recorder = _Recorder()
        _run(monkeypatch, ["web-checks", *many], recorder)
        assert "whole scope" in capsys.readouterr().out

    def test_a_skipped_tool_is_reported_as_skipped_not_instant(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # A filter that silently stopped matching would report the same
        # ``0.0s`` as a healthy fast pass, forever.
        recorder = _Recorder()
        _run(monkeypatch, ["web-checks", "web/package.json"], recorder)
        assert "eslint skipped" in capsys.readouterr().out

    @pytest.mark.parametrize(
        "token",
        [
            pytest.param("lint:knip", id="knip"),
            pytest.param("lint:circular", id="circular"),
        ],
    )
    def test_tools_without_a_pattern_ignore_filenames(
        self, monkeypatch: pytest.MonkeyPatch, token: str
    ) -> None:
        recorder = _Recorder()
        _run(monkeypatch, ["web-checks", "web/src/pages/Thing.tsx"], recorder)
        assert "web/src/pages/Thing.tsx" not in recorder.argv_for(token)
