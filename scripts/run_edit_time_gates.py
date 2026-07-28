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
not, adds no rule of its own, and is excluded from CI parity like the two
sibling PostToolUse audits. Every gate it invokes remains the authority on
its own scope and opt-outs; this file only decides which gates could
possibly have an opinion about the path that changed, and each gate then
re-filters the path itself, so a routing mistake here can only ever cost a
wasted subprocess, never a missed or invented violation.

Usage (hook mode -- reads JSON from stdin):
    echo '{"tool_input":{"file_path":"src/synthorg/foo.py"}}' |
        python scripts/run_edit_time_gates.py

Usage (CLI mode):
    python scripts/run_edit_time_gates.py src/synthorg/foo.py

Exit codes:
    0 -- every applicable gate passed, or the path is out of scope for all
         of them (the common case: a Markdown or TypeScript edit).
    1 -- at least one gate reported a violation.
"""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

# Each gate gets its own bound rather than one for the batch: the point of
# this hook is a fast loop, and one pathological file must not stall the
# others. Generous next to a warm single-file AST parse, which is
# milliseconds; a gate that needs longer than this has a defect worth
# seeing rather than waiting out.
_GATE_TIMEOUT_SECONDS: Final[int] = 20


@dataclass(frozen=True, slots=True)
class _Gate:
    """One gate this dispatcher can route a file to.

    Args:
        script: Gate filename under ``scripts/``.
        flag: The gate's file-scoping flag. ``None`` means it takes bare
            positional paths (``check_no_review_origin_in_code``).
        suffixes: File extensions the gate reads.
        roots: Repo-relative trees the gate scopes to. A path under none of
            them is not routed here.
    """

    script: str
    flag: str | None
    suffixes: frozenset[str]
    roots: tuple[str, ...]

    def applies_to(self, rel: str, suffix: str) -> bool:
        """Return whether *rel* could possibly interest this gate."""
        if suffix not in self.suffixes:
            return False
        return any(rel == root or rel.startswith(f"{root}/") for root in self.roots)

    def argv(self, rel: str) -> list[str]:
        """Return the full command line for scanning *rel*."""
        base = [sys.executable, str(_REPO_ROOT / "scripts" / self.script)]
        return [*base, rel] if self.flag is None else [*base, self.flag, rel]


# Only gates whose verdict for one file is independent of every other file
# belong here. A gate that needs cross-file context (import graph, endpoint
# parity, dual-backend test pairing, baseline drift over the whole tree)
# would report a false violation from a single-file scan, so it stays
# pre-push-only no matter how cheap it looks.
_GATES: Final[tuple[_Gate, ...]] = (
    _Gate(
        script="check_no_stubs.py",
        flag="--files",
        suffixes=frozenset({".py"}),
        roots=("src/synthorg",),
    ),
    _Gate(
        script="check_frozen_model_extra_forbid.py",
        flag="--files",
        suffixes=frozenset({".py"}),
        roots=("src/synthorg", "tests"),
    ),
    _Gate(
        script="check_no_magic_numbers.py",
        flag="--files",
        suffixes=frozenset({".py"}),
        roots=("src/synthorg",),
    ),
    _Gate(
        script="check_module_size_budget.py",
        flag="--files",
        suffixes=frozenset({".py"}),
        roots=("src/synthorg",),
    ),
    _Gate(
        script="check_no_review_origin_in_code.py",
        flag=None,
        suffixes=frozenset({".py", ".sql"}),
        roots=("src/synthorg", "tests"),
    ),
)


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


def _relative_path(raw: str) -> str | None:
    """Return *raw* as a repo-relative POSIX path, or ``None`` if outside.

    A path outside the repo (or one that no longer exists, because the edit
    was a delete) has no gate that applies to it.
    """
    candidate = Path(raw)
    resolved = (
        candidate if candidate.is_absolute() else _REPO_ROOT / candidate
    ).resolve()
    if not resolved.is_file():
        return None
    try:
        return resolved.relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return None


def _run_gate(gate: _Gate, rel: str) -> _Result:
    """Invoke *gate* against *rel* and capture its verdict.

    A gate that crashes or times out is reported as a failure rather than
    swallowed: this hook is advisory about the tree, never about itself, and
    silently degrading to "clean" is the one outcome that would make it
    worse than not existing.
    """
    try:
        completed = subprocess.run(
            gate.argv(rel),
            capture_output=True,
            text=True,
            cwd=_REPO_ROOT,
            check=False,
            timeout=_GATE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return _Result(
            gate.script,
            1,
            f"{gate.script}: timed out after {_GATE_TIMEOUT_SECONDS}s",
        )
    except OSError as exc:
        return _Result(gate.script, 1, f"{gate.script}: could not run ({exc})")
    merged = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    return _Result(gate.script, completed.returncode, merged.strip())


def _file_path_from_stdin() -> str | None:
    """Read ``tool_input.file_path`` from the PostToolUse hook payload.

    Mirrors the stdin contract of the two sibling PostToolUse audits so all
    three are wired identically in ``.claude/settings.json`` and the
    OpenCode plugin.
    """
    if sys.stdin.isatty():
        return None
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError, UnicodeDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    raw = tool_input.get("file_path")
    return raw if isinstance(raw, str) and raw else None


def main(argv: list[str] | None = None) -> int:
    """Route one edited file to every gate that scopes to it."""
    args = sys.argv[1:] if argv is None else argv
    raw = args[0] if args else _file_path_from_stdin()
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

    print(f"EDIT-TIME GATE VIOLATIONS in {rel}:")
    print()
    for failure in failures:
        print(f"--- {failure.script}")
        if failure.output:
            print(failure.output)
    print()
    print(
        "These are the same gates the pre-push run enforces whole-tree. "
        "Fixing them now keeps them off the push budget. Each gate's own "
        "output names its per-line opt-out where it has one."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
