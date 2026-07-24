#!/usr/bin/env python3
"""Run a group of independent pre-push tools concurrently.

pre-commit executes hooks one after another. Several of the pre-push
tools are wholly independent of each other -- they read the tree, write
nothing, and share no state -- so paying for them in sequence is dead
wall-clock on every push. A push is held to a five-minute budget, and
measurement put the sequential tail of these groups at well over a
minute of that.

Each group below is a set of tools that can safely overlap. The runner
starts them together, waits for all of them (never short-circuits: a
developer wants every failure from one push, not the first one), prints
each tool's duration so a future regression is visible rather than felt,
and exits non-zero if any tool did.

The same idiom already exists inline for ``sqlfluff``, which runs its
SQLite and Postgres dialects concurrently; this generalises it so the
groups stay declarative and testable instead of growing as ever-longer
``bash -c`` one-liners.

Filenames: pre-commit appends the matched files as trailing arguments.
Only a tool declaring a ``filename_pattern`` receives them, and then only
the subset its own pattern accepts; every other tool decides its own
scope, exactly as it did as a standalone hook.

Usage:
    python scripts/run_prepush_hook_group.py <group> [--] [FILE ...]

Exit codes:
    0 -- every tool in the group passed.
    1 -- at least one tool failed; EVERY failing tool's output is printed,
         since the run never stops at the first failure.
    2 -- unknown group name, or a group whose declaration is malformed.
"""

import argparse
import contextlib
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
# POSIX convention for "command not found", reused so a tool that cannot
# start reads like any other tool failure rather than an interpreter crash.
_EXIT_NOT_FOUND: Final[int] = 127


@dataclass(frozen=True, slots=True)
class _Tool:
    """One tool in a concurrent group.

    Args:
        name: Label shown in the per-tool timing line.
        argv: Command to run, from the repository root.
        filename_pattern: When set, the tool receives the matched files
            this pattern accepts. A group's pre-commit ``files:`` is the
            union of its tools' interests, so a file-taking tool must
            re-filter: handing ESLint a ``package.json`` that only ``knip``
            cares about would fail the run on an unlintable path.
    """

    name: str
    argv: tuple[str, ...]
    filename_pattern: re.Pattern[str] | None = None

    def __post_init__(self) -> None:
        """Reject a malformed declaration at import time.

        A blank name misattributes the report and an empty argv only
        surfaces as an IndexError inside a worker thread, mid-push.

        Raises:
            ValueError: When the name is blank or the argv is empty.
        """
        if not self.name.strip():
            msg = "_Tool.name must not be blank"
            raise ValueError(msg)
        if not self.argv:
            msg = f"_Tool.argv must not be empty (tool {self.name!r})"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class _Result:
    """The outcome of one tool run.

    ``skipped`` separates "had no files to look at" from "ran and passed
    instantly". Without it a filter that silently stopped matching would
    report the same ``0.0s`` as a healthy fast pass forever, which is
    precisely the regression this runner's timing output exists to expose.
    """

    name: str
    returncode: int
    output: str
    seconds: float
    skipped: bool = False

    @property
    def ok(self) -> bool:
        """Whether the tool raised no objection.

        Returns:
            ``True`` when the tool passed or was skipped.
        """
        return self.returncode == 0

    def render(self) -> str:
        """Return the tool's entry for the timing summary.

        Returns:
            A label naming a skip explicitly rather than as a duration.
        """
        return (
            f"{self.name} skipped"
            if self.skipped
            else (f"{self.name} {self.seconds:.1f}s")
        )


# Groups of tools that are independent of one another. A tool belongs in a
# group only if it neither writes to the tree nor depends on another tool's
# output; anything with a shared artefact stays its own sequential hook.
_ESLINT_FILES: Final[re.Pattern[str]] = re.compile(
    r"^web/(src|test-infra)/.*\.(ts|tsx)$"
)

_GROUPS: Final[Mapping[str, tuple[_Tool, ...]]] = MappingProxyType(
    {
        "python-audits": (
            _Tool("vulture", ("uv", "run", "vulture")),
            _Tool(
                "interrogate",
                ("uv", "run", "interrogate", "-c", "pyproject.toml", "src/synthorg"),
            ),
            _Tool("deptry", ("uv", "run", "deptry", "src/synthorg")),
        ),
        "web-checks": (
            _Tool(
                "eslint",
                (
                    "npm",
                    "--prefix",
                    "web",
                    "exec",
                    "--",
                    "eslint",
                    "--max-warnings",
                    "0",
                    # A changed set can include generated files (``*.gen.ts``)
                    # that eslint's own config ignores. Handing eslint an
                    # explicitly-ignored path makes it emit a "File ignored"
                    # warning, which ``--max-warnings 0`` then fails on. Defer
                    # to eslint's ignore config as the single source of truth:
                    # skip such a file silently instead of failing the push on
                    # a file no one was ever meant to lint.
                    "--no-warn-ignored",
                ),
                filename_pattern=_ESLINT_FILES,
            ),
            _Tool("knip", ("npm", "--prefix", "web", "run", "lint:knip")),
            _Tool("circular", ("npm", "--prefix", "web", "run", "lint:circular")),
        ),
    },
)


def _validate_groups(groups: Mapping[str, tuple[_Tool, ...]]) -> None:
    """Reject a malformed group registry at import time.

    An empty group would exit 0 having run nothing, and a duplicate name
    would collapse two tools into one line of the report.

    Raises:
        ValueError: When a group is empty or repeats a tool name.
    """
    for group, tools in groups.items():
        if not tools:
            msg = f"group {group!r} declares no tools"
            raise ValueError(msg)
        names = [tool.name for tool in tools]
        if len(names) != len(set(names)):
            msg = f"group {group!r} repeats a tool name: {sorted(names)}"
            raise ValueError(msg)


_validate_groups(_GROUPS)


def _run(tool: _Tool, filenames: Sequence[str]) -> _Result:
    """Run one tool and capture its combined output.

    Never raises: a tool that cannot start is reported as a failed
    ``_Result`` like any other. An exception escaping here would surface
    from ``pool.map`` as a bare traceback and discard the results of every
    sibling that had already finished, which is exactly the
    stop-at-the-first-problem behaviour this runner exists to avoid.

    Returns:
        The tool's :class:`_Result`.
    """
    argv = list(tool.argv)
    if tool.filename_pattern is not None:
        matched = [f for f in filenames if tool.filename_pattern.match(f)]
        if not matched:
            # Nothing in this push is the tool's concern. Invoking it with
            # no paths would make some tools fall back to their whole
            # configured scope, which is the cost this runner exists to
            # avoid, so skip it outright.
            return _Result(tool.name, 0, "", 0.0, skipped=True)
        argv.extend(matched)
    # Windows ships ``npm`` as ``npm.cmd``; CreateProcess will not resolve
    # the bare name, and ``shell=True`` would hand the shell a path list
    # taken from git output. Resolving on PATH keeps the argv form exact.
    resolved = shutil.which(argv[0])
    if resolved is None:
        return _Result(
            tool.name,
            _EXIT_NOT_FOUND,
            f"{argv[0]!r} is not on PATH; the tool could not be started.",
            0.0,
        )
    argv[0] = resolved
    started = time.monotonic()  # lint-allow: clock-seam -- gate script, no DI
    try:
        completed = subprocess.run(
            argv,
            cwd=_REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        # lint-allow: clock-seam -- gate script, no DI
        return _Result(
            tool.name,
            _EXIT_NOT_FOUND,
            f"failed to start: {type(exc).__name__}: {exc}",
            time.monotonic() - started,
        )
    # lint-allow: clock-seam -- gate script, no DI
    elapsed = time.monotonic() - started
    return _Result(
        tool.name,
        completed.returncode,
        completed.stdout + completed.stderr,
        elapsed,
    )


def _run_group(tools: tuple[_Tool, ...], filenames: Sequence[str]) -> list[_Result]:
    """Run every tool in the group concurrently.

    Returns:
        One result per tool, in the group's declared order.
    """
    with ThreadPoolExecutor(max_workers=len(tools)) as pool:
        return list(pool.map(lambda tool: _run(tool, filenames), tools))


def main() -> int:
    """Run the named group.

    Returns:
        The process exit code (0 clean, 1 a tool failed, 2 unknown group).
    """
    # ESLint (and other Node tools) print a ✖ summary and box-drawing
    # glyphs the Windows console's default cp1252 codec cannot encode, so
    # relaying their captured UTF-8 output through a bare ``print`` raises
    # UnicodeEncodeError and crashes the runner that is only the messenger
    # -- turning a real lint failure into an unreadable traceback. Re-encode
    # our own streams as UTF-8 with replacement so a tool's Unicode
    # diagnostic can never take the runner down with it.
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("group", help="group name from _GROUPS")
    parser.add_argument("filenames", nargs="*", help="files pre-commit matched")
    args = parser.parse_args()

    tools = _GROUPS.get(args.group)
    if tools is None:
        known = ", ".join(sorted(_GROUPS))
        print(f"error: unknown group {args.group!r}; known: {known}", file=sys.stderr)
        return 2

    started = time.monotonic()  # lint-allow: clock-seam -- gate script, no DI
    results = _run_group(tools, args.filenames)
    # lint-allow: clock-seam -- gate script, no DI
    total = time.monotonic() - started

    failures = [result for result in results if not result.ok]
    for result in failures:
        # Only a failing tool's output is worth the noise; a passing tool
        # reports its duration so a slowdown is visible on a green run.
        print(f"\n--- {result.name} (exit {result.returncode}) ---")
        print(result.output.rstrip())
    timings = ", ".join(result.render() for result in results)
    print(f"\n{args.group}: {timings} -- {total:.1f}s wall-clock")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
