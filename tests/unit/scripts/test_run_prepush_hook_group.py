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
import threading
from collections.abc import Callable, Mapping
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
    """The two fields the tests read off a declared tool."""

    name: str
    argv: tuple[str, ...]


class _GateModule(Protocol):
    """Subset of ``scripts/run_prepush_hook_group.py`` the tests exercise.

    The declarations are the runner's own private names; the tests reach
    for them because the registry's validity is part of the contract, not
    an implementation detail: a malformed group would exit 0 having run
    nothing.
    """

    _Tool: Callable[[str, tuple[str, ...]], _ToolShape]
    _GROUPS: Mapping[str, tuple[_ToolShape, ...]]
    _validate_groups: Callable[[Mapping[str, tuple[object, ...]]], None]

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
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __call__(
        self, argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        with self.lock:
            self.calls.append(list(argv))
            self.kwargs.append(dict(kwargs))
        returncode = next(
            (code for token, code in self.returncodes.items() if token in argv), 0
        )
        return subprocess.CompletedProcess(argv, returncode, "out", "err")

    def argv_for(self, token: str) -> list[str]:
        """Return the argv of the single call containing *token*.

        Raises:
            AssertionError: When no call, or more than one, matches.
        """
        matches = [call for call in self.calls if token in call]
        assert len(matches) == 1, f"expected one {token!r} call, got {len(matches)}"
        return matches[0]

    def ran(self, token: str) -> bool:
        """Whether any launched command contained *token*.

        Returns:
            ``True`` when at least one call's argv carries the token.
        """
        return any(token in call for call in self.calls)


def _patch(monkeypatch: pytest.MonkeyPatch, recorder: _Recorder) -> None:
    monkeypatch.setattr(subprocess, "run", recorder)
    # ``shutil.which`` resolves npm to npm.cmd on Windows; the tests care
    # about the argument list, not the resolved interpreter path. The
    # runner imported this same module object, so patching it here reaches
    # the call site.
    monkeypatch.setattr(shutil, "which", lambda name: name)


def _run(monkeypatch: pytest.MonkeyPatch, argv: list[str], recorder: _Recorder) -> int:
    _patch(monkeypatch, recorder)
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

        monkeypatch.setattr(subprocess, "run", _explode)
        monkeypatch.setattr(shutil, "which", lambda name: name)
        monkeypatch.setattr("sys.argv", ["run_prepush_hook_group.py", "python-audits"])

        assert _MODULE.main() == 1
        assert "failed to start: OSError" in capsys.readouterr().out

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
            assert call_kwargs["capture_output"] is True
            assert call_kwargs["check"] is False

    def test_output_is_decoded_leniently(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A tool emitting a stray byte must not crash the group with a
        # UnicodeDecodeError in place of its own diagnostic.
        recorder = _Recorder()
        _run(monkeypatch, ["python-audits"], recorder)
        for call_kwargs in recorder.kwargs:
            assert call_kwargs["text"] is True
            assert call_kwargs["errors"] == "replace"


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
