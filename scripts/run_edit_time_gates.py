#!/usr/bin/env python3
"""Run the file-scoped convention gates against one just-edited file.

Most of the gate inventory only runs at pre-push, whole-tree. That is the
right scope for the push, but it makes the feedback loop for a handful of
purely local rules far longer than it needs to be: a stub, a frozen model
missing ``extra="forbid"``, a bare numeric literal, a module that just
crossed its tier cap, or a reviewer citation in a comment is decidable from
the edited file alone, yet is discovered minutes later at push time, where
it burns the push budget and leaves a ``<hook>-FAILED`` marker that blocks
the next push until cleared.

This dispatcher closes that loop. It is a PostToolUse hook: the write has
already landed, so it does not block anything. It reports, and the agent
fixes the violation while the change is still in hand.

It is deliberately NOT a gate. It enforces nothing the pre-push run does
not, adds no rule of its own, and registers nothing in
``convention_gate_map.yaml``. Like the two sibling PostToolUse audits it has
no CI counterpart, for a mechanical reason worth stating plainly: none of the
three is registered in ``.pre-commit-config.yaml`` at all, so the CI-parity
gate never enumerates them. That gate's docstring only spells this out for
PreToolUse hooks.

Every gate it invokes remains the authority on its own scope and opt-outs;
this file only decides which gates could possibly have an opinion about the
path that changed, and each gate then re-filters the path itself. A gate that
is handed a path it does not want says so on stderr rather than reporting a
silent clean scan, so the two scope tables drifting apart is visible instead
of quietly narrowing coverage. ``test_dispatcher_roots_match_gate_scan_roots``
pins them together.

Usage (hook mode -- reads JSON from stdin):
    echo '{"tool_input":{"file_path":"src/synthorg/foo.py"}}' |
        python scripts/run_edit_time_gates.py

Usage (CLI mode):
    python scripts/run_edit_time_gates.py src/synthorg/foo.py

Exit codes:
    0 -- every applicable gate passed, or the path is out of scope for all
         of them (the common case: a Markdown or TypeScript edit).
    1 -- at least one gate reported a violation, or could not complete its
         scan. The report distinguishes the two.
"""

import argparse
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
# Where the gate scripts live, kept separate from _REPO_ROOT on purpose.
# _REPO_ROOT is what edited paths resolve against and what the gates run with
# as their cwd; this is where the gate implementations are. Deriving both from
# one constant would tie "which tree is being scanned" to "which tree's gates
# are running", which is wrong in the one case where they differ.
_SCRIPTS_DIR: Final[Path] = Path(__file__).resolve().parent

# Each gate gets its own bound rather than one for the batch: the point of
# this hook is a fast loop, and one pathological file must not stall the
# others. Measured, every routed gate answers for a 1000-line file in under
# 100ms, so this is a ~100x margin; a gate that needs longer than this has a
# defect worth seeing rather than waiting out.
_GATE_TIMEOUT_SECONDS: Final[int] = 20
# Once the tree is dead the pipes are closed, so the follow-up drain returns
# at once. Still bounded: an unbounded drain is the bug being avoided here.
_DRAIN_TIMEOUT_SECONDS: Final[int] = 5
_TREE_KILL_TIMEOUT_SECONDS: Final[int] = 15
# A gate's own "I could not trust this scan" code. Distinct from a violation,
# and reported differently, because the two call for completely different
# responses: fix the tree, versus fix the tooling.
_EXIT_SCAN_ERROR: Final[int] = 2
# Dispatcher-side failures (could not launch, wedged, crashed) use their own
# code so they are never mistaken for either of the gate's own verdicts.
_EXIT_DISPATCH_ERROR: Final[int] = 3


def _terminate_tree(process: subprocess.Popen[str]) -> None:
    """Kill a wedged gate and everything it spawned.

    ``Popen.kill`` signals only the direct child, which is not enough. Two of
    the routed gates shell out to ``git ls-files`` on their whole-tree path,
    and a grandchild that survives holds the inherited stdout write-end open,
    so the drain that follows a timeout never sees EOF and the hook hangs
    without bound despite the timeout having fired. The sibling pre-push
    runner records that exact failure as a 180s budget overrunning to 1034s.
    """
    if sys.platform == "win32":
        taskkill = shutil.which("taskkill")
        if taskkill is not None:
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                subprocess.run(
                    [taskkill, "/T", "/F", "/PID", str(process.pid)],
                    check=False,
                    capture_output=True,
                    timeout=_TREE_KILL_TIMEOUT_SECONDS,
                )
    else:
        with contextlib.suppress(OSError):
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    with contextlib.suppress(OSError):
        process.kill()


@dataclass(frozen=True, slots=True)
class _Gate:
    """One gate this dispatcher can route a file to.

    Args:
        script: Gate filename under ``scripts/``, a bare name with no
            directory separator.
        flag: The gate's file-scoping flag. ``None`` means it takes bare
            positional paths (``check_no_review_origin_in_code``).
        suffixes: File extensions the gate reads.
        roots: Repo-relative POSIX prefixes the gate scopes to, each without a
            trailing slash. Unordered: ``applies_to`` is a pure existential
            check with no first-match-wins semantics.
    """

    script: str
    flag: str | None
    suffixes: frozenset[str]
    roots: frozenset[str]

    def __post_init__(self) -> None:
        """Reject a malformed entry at import time.

        Without this the failure mode is silent and permanent rather than
        loud: a root written ``"src/synthorg/"`` makes ``applies_to`` build
        ``"src/synthorg//"``, which matches no real path, so the gate stops
        firing forever with nothing to indicate why. An aggregate
        ``any(...)`` assertion over the registry cannot catch it either, since
        a sibling gate covering the same root keeps the test green.

        Raises:
            ValueError: If the script name or any root is malformed.
        """
        if not self.script or "/" in self.script or "\\" in self.script:
            msg = f"gate script must be a bare filename, got {self.script!r}"
            raise ValueError(msg)
        if not self.roots:
            msg = f"{self.script}: roots must not be empty"
            raise ValueError(msg)
        for root in self.roots:
            if not root or root.startswith("/") or root.endswith("/"):
                msg = (
                    f"{self.script}: root {root!r} must be non-empty with no "
                    "leading or trailing slash"
                )
                raise ValueError(msg)
            if "\\" in root:
                msg = f"{self.script}: root {root!r} must be POSIX-separated"
                raise ValueError(msg)
        if not self.suffixes:
            msg = f"{self.script}: suffixes must not be empty"
            raise ValueError(msg)

    def applies_to(self, rel: str, suffix: str) -> bool:
        """Return whether *rel* could possibly interest this gate.

        Args:
            rel: Repo-relative POSIX path.
            suffix: The path's file extension.

        Returns:
            ``True`` if the suffix matches and *rel* sits under a scan root.
        """
        if suffix not in self.suffixes:
            return False
        return any(rel == root or rel.startswith(f"{root}/") for root in self.roots)

    def argv(self, rel: str) -> list[str]:
        """Return the full command line for scanning *rel*.

        Args:
            rel: Repo-relative POSIX path to scan.

        Returns:
            The argv list, with the flag inserted for a flag-taking gate.
        """
        base = [sys.executable, str(_SCRIPTS_DIR / self.script)]
        return [*base, rel] if self.flag is None else [*base, self.flag, rel]


# Only gates whose verdict for one file is independent of every other file
# belong here. The distinction that matters is not "does it read a baseline":
# a baseline that is a static, already-committed per-path lookup (as the
# module-size and magic-number gates use) gives a single-file scan the same
# verdict the whole-tree run would give that file. What disqualifies a gate is
# needing the whole tree to compute its answer at all -- an import graph,
# endpoint parity, dual-backend test pairing, or a suppression population
# derived by diffing two whole-tree lint passes, as
# check_argument_count_suppression does. Those would report a false violation
# from a single file, so they stay pre-push-only however cheap they look.
_GATES: Final[tuple[_Gate, ...]] = (
    _Gate(
        script="check_no_stubs.py",
        flag="--files",
        suffixes=frozenset({".py"}),
        roots=frozenset({"src/synthorg"}),
    ),
    _Gate(
        script="check_frozen_model_extra_forbid.py",
        flag="--files",
        suffixes=frozenset({".py"}),
        roots=frozenset({"src/synthorg", "tests"}),
    ),
    _Gate(
        script="check_no_magic_numbers.py",
        flag="--files",
        suffixes=frozenset({".py"}),
        roots=frozenset({"src/synthorg"}),
    ),
    _Gate(
        script="check_module_size_budget.py",
        flag="--files",
        suffixes=frozenset({".py"}),
        roots=frozenset({"src/synthorg"}),
    ),
    _Gate(
        script="check_no_review_origin_in_code.py",
        flag=None,
        suffixes=frozenset({".py", ".sql"}),
        roots=frozenset({"src/synthorg", "tests"}),
    ),
)

if len({gate.script for gate in _GATES}) != len(_GATES):
    _DUPLICATE_MSG: Final[str] = "_GATES contains two entries for one script"
    raise AssertionError(_DUPLICATE_MSG)


@dataclass(frozen=True, slots=True)
class _Result:
    """One gate's outcome for the scanned file."""

    script: str
    returncode: int
    output: str

    @property
    def failed(self) -> bool:
        """Return whether the gate reported a violation or could not run."""
        return self.returncode != 0

    @property
    def label(self) -> str:
        """Return a tag separating a tree defect from a tooling defect."""
        if self.returncode == _EXIT_SCAN_ERROR:
            return "gate could not complete its scan (tooling defect)"
        if self.returncode == _EXIT_DISPATCH_ERROR:
            return "gate could not be run (dispatcher defect)"
        return "violation"


def _relative_path(raw: str) -> str | None:
    """Return *raw* as a repo-relative POSIX path, or ``None`` if outside.

    ``_REPO_ROOT / candidate`` needs no ``is_absolute`` branch: joining a path
    with an absolute right-hand operand already discards the left one. The
    ``as_posix()`` normalisation is what lets a native Windows backslash path
    from the hook payload reach ``applies_to`` in the form its roots use.

    Args:
        raw: Path from the hook payload or the CLI.

    Returns:
        The repo-relative POSIX path, or ``None`` for a path outside the repo,
        one that no longer exists (the edit was a delete), or one the OS
        refuses to resolve.
    """
    try:
        resolved = (_REPO_ROOT / Path(raw)).resolve()
    except OSError:
        # An embedded NUL, a reserved Windows device name, or an unreachable
        # network path. Not something any gate can have an opinion about.
        return None
    if not resolved.is_file():
        return None
    try:
        return resolved.relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return None


def _run_gate(gate: _Gate, rel: str) -> _Result:
    """Invoke *gate* against *rel* and capture its verdict.

    Never raises. Any failure becomes a reported ``_Result``, because this
    hook is advisory about the tree and never about itself: silently
    degrading to "clean" is the one outcome that would make it worse than not
    existing, and letting an exception escape would additionally discard the
    verdicts of every gate that had already finished.

    ``Popen`` rather than ``subprocess.run(timeout=...)``: run's timeout path
    kills only the direct child and then drains unbounded, so a surviving
    grandchild wedges the hook. Explicit ``encoding`` because the default
    would decode gate output with the platform locale codec, and a violation
    message quoting non-Latin-1 source text would then raise mid-decode.

    Args:
        gate: The gate to invoke.
        rel: Repo-relative POSIX path to scan.

    Returns:
        The gate's outcome, including for a wedged or unlaunchable gate.
    """
    try:
        with subprocess.Popen(
            gate.argv(rel),
            cwd=_REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=sys.platform != "win32",
        ) as process:
            try:
                stdout, stderr = process.communicate(timeout=_GATE_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                _terminate_tree(process)
                try:
                    stdout, stderr = process.communicate(timeout=_DRAIN_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    stdout, stderr = "", ""
                partial = f"{stdout}{stderr}".rstrip()
                detail = f"\nOutput before it wedged:\n{partial}" if partial else ""
                return _Result(
                    gate.script,
                    _EXIT_DISPATCH_ERROR,
                    f"timed out after {_GATE_TIMEOUT_SECONDS}s and was killed "
                    f"with its process tree.{detail}",
                )
            returncode = process.returncode
    except OSError as exc:
        return _Result(
            gate.script,
            _EXIT_DISPATCH_ERROR,
            f"failed to start: {type(exc).__name__}: {exc}",
        )
    except Exception as exc:
        return _Result(
            gate.script,
            _EXIT_DISPATCH_ERROR,
            f"crashed the dispatcher: {type(exc).__name__}: {exc}",
        )
    merged = "\n".join(part for part in (stdout, stderr) if part)
    return _Result(gate.script, returncode, merged.strip())


def _file_path_from_stdin() -> str | None:
    """Read ``tool_input.file_path`` from the PostToolUse hook payload.

    Warns on every malformed shape rather than returning a bare ``None``. A
    silent parse failure would exit 0, which this hook reads as "clean" and a
    developer reads as "checked": exactly the outcome this file exists to
    prevent, and the failure mode a harness payload-schema change would take.
    (The two sibling audits also warn here, though they diverge afterwards:
    one exits 2 via argparse, the other exits 0. There is no single sibling
    contract to mirror, only the shared decision not to stay quiet.)

    Returns:
        The path string, or ``None`` when stdin is a terminal or the payload
        does not carry one.
    """
    if sys.stdin.isatty():
        return None
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(
            f"WARNING: run_edit_time_gates: could not parse hook JSON from "
            f"stdin ({type(exc).__name__}); no gate ran.",
            file=sys.stderr,
        )
        return None
    if not isinstance(payload, dict):
        print(
            "WARNING: run_edit_time_gates: hook payload is not a JSON object; "
            "no gate ran.",
            file=sys.stderr,
        )
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        print(
            "WARNING: run_edit_time_gates: hook payload has no tool_input "
            "object; no gate ran.",
            file=sys.stderr,
        )
        return None
    raw = tool_input.get("file_path")
    if isinstance(raw, str) and raw:
        return raw
    # Registered on Edit|Write, which always carries a file_path, so its
    # absence means the payload schema moved under us. Reporting that is the
    # whole point: staying quiet would read as "checked and clean".
    print(
        "WARNING: run_edit_time_gates: hook payload carries no usable "
        "tool_input.file_path; no gate ran.",
        file=sys.stderr,
    )
    return None


def _report(rel: str, failures: list[_Result]) -> None:
    """Print every failing gate's output, tagged by what kind of failure it is."""
    print(f"EDIT-TIME GATE FINDINGS in {rel}:")
    print()
    for failure in failures:
        print(f"--- {failure.script} (exit {failure.returncode}: {failure.label})")
        if failure.output:
            print(failure.output)
    print()
    print(
        "These are the same gates the pre-push run enforces whole-tree. "
        "Fixing them now keeps them off the push budget. Each gate's own "
        "output names its per-line opt-out where it has one. An entry tagged "
        "as a tooling or dispatcher defect is not a problem with your code."
    )


def main(argv: list[str] | None = None) -> int:
    """Route one edited file to every gate that scopes to it.

    Args:
        argv: Argument list; ``None`` reads ``sys.argv``.

    Returns:
        ``0`` when nothing applicable failed, ``1`` otherwise.
    """
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "file_path",
        nargs="?",
        default=None,
        help="File to check. Omit it to read the hook payload from stdin.",
    )
    args = parser.parse_args(argv)

    raw = args.file_path or _file_path_from_stdin()
    if not raw:
        return 0

    rel = _relative_path(raw)
    if rel is None:
        return 0
    suffix = Path(rel).suffix
    applicable = [gate for gate in _GATES if gate.applies_to(rel, suffix)]
    if not applicable:
        return 0

    with ThreadPoolExecutor(max_workers=len(applicable)) as pool:
        results = list(pool.map(lambda gate: _run_gate(gate, rel), applicable))

    failures = [result for result in results if result.failed]
    if not failures:
        return 0
    _report(rel, failures)
    return 1


if __name__ == "__main__":
    sys.exit(main())
