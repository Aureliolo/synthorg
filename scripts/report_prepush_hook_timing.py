#!/usr/bin/env python3
"""Report per-hook wall-clock for a whole pre-push run.

The push is held to a 300s budget, but nothing reports where that budget
goes. pre-commit runs hooks strictly sequentially and already times each
one, printing ``- duration: <n>s`` whenever the run or the hook is
verbose; today only three hooks set ``verbose: true``, so the number
exists and is thrown away. This surfaces it rather than re-timing the
hooks from the outside, which would measure a second, differently-scoped
run of the same work.

Two group runners report their own internal breakdown on stdout
(``run_prepush_python_gates.py`` one line per gate, ``run_prepush_hook_group.py``
one line per tool). pre-commit prints a hook's captured output directly
after that hook's id, so those lines are attributed to the hook above
them and the table can drill into the two consolidated hooks instead of
reporting them as one opaque block.

This is a diagnostic, never a gate: it is wired into no hook, no
``_GATES`` tuple and no group, so it costs nothing on a push. The
``report_`` prefix keeps it out of the ``scripts/check_*.py`` namespace
that the wiring-safety gate enumerates.

Usage::

    python scripts/report_prepush_hook_timing.py
    python scripts/report_prepush_hook_timing.py --skip mypy,pytest-unit
    python scripts/report_prepush_hook_timing.py --json before.json
    python scripts/report_prepush_hook_timing.py --from-log prepush-last.log

``--all-files`` is the default because a diff-scoped run measures
whichever files happen to be on the branch, which is not comparable
across two runs. Compare like with like, then read the ``files:``
triggers to decide which hooks a given push would actually pay for.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_DESCRIPTION: Final[str] = "Report per-hook wall-clock for a whole pre-push run."
_DEFAULT_STAGE: Final[str] = "pre-push"
_STAGES: Final[tuple[str, ...]] = ("pre-push", "pre-commit")
_RUN_TIMEOUT_SECONDS: Final[float] = 3600.0

# pre-commit's own reporting format (pre_commit/commands/run.py). A skipped
# hook prints an id with no duration, so the two are matched independently.
_HOOK_ID_RE: Final[re.Pattern[str]] = re.compile(r"^\s*- hook id:\s*(\S+)\s*$")
_DURATION_RE: Final[re.Pattern[str]] = re.compile(r"^\s*- duration:\s*([\d.]+)s\s*$")

# run_prepush_python_gates.py: "  [ok  ]   0.4s  check_no_stubs"
_GATE_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*\[(ok|FAIL)\s*\]\s*([\d.]+)s\s+(\S+)\s*$"
)
# run_prepush_hook_group.py: "web-checks: eslint 8.1s, knip 3.0s -- 8.2s wall-clock"
_GROUP_RE: Final[re.Pattern[str]] = re.compile(
    r"^(\S+): (.+) -- ([\d.]+)s wall-clock\s*$"
)
_GROUP_TOOL_RE: Final[re.Pattern[str]] = re.compile(r"([A-Za-z0-9_.-]+) ([\d.]+)s")

_TABLE_WIDTH: Final[int] = 52
_SUB_TABLE_LIMIT: Final[int] = 8


@dataclass(slots=True)
class HookTiming:
    """One hook's measured cost, with any sub-tool breakdown it reported."""

    hook_id: str
    seconds: float | None = None
    children: dict[str, float] = field(default_factory=dict)
    runs: int = 0

    @property
    def skipped(self) -> bool:
        """Whether pre-commit skipped this hook (no duration is emitted)."""
        return self.seconds is None


def _record_children(current: HookTiming | None, line: str) -> None:
    """Attach any group-runner breakdown on ``line`` to the current hook.

    A tool's seconds are accumulated rather than assigned. pre-commit
    partitions a long filename list into several argv-sized chunks and
    invokes the hook once per chunk, reporting one summed duration, so a
    runner that prints per invocation prints several times. Overwriting
    would report the last chunk as if it were the whole hook and hide
    every whole-tree tool that just ran N times over.
    """
    if current is None:
        return
    gate = _GATE_RE.match(line)
    if gate is not None:
        stem, seconds = gate.group(3), float(gate.group(2))
        current.children[stem] = current.children.get(stem, 0.0) + seconds
        return
    group = _GROUP_RE.match(line)
    if group is not None:
        current.runs += 1
        for name, seconds in _GROUP_TOOL_RE.findall(group.group(2)):
            current.children[name] = current.children.get(name, 0.0) + float(seconds)


def parse_timings(text: str) -> list[HookTiming]:
    """Parse pre-commit's verbose output into per-hook timings.

    Args:
        text: Combined stdout/stderr of a verbose ``pre-commit run``.

    Returns:
        One entry per reported hook, in the order pre-commit ran them.
    """
    timings: list[HookTiming] = []
    current: HookTiming | None = None
    for line in text.splitlines():
        hook = _HOOK_ID_RE.match(line)
        if hook is not None:
            current = HookTiming(hook_id=hook.group(1))
            timings.append(current)
            continue
        duration = _DURATION_RE.match(line)
        if duration is not None and current is not None:
            current.seconds = float(duration.group(1))
            continue
        _record_children(current, line)
    return timings


def _run_pre_commit(stage: str, skip: str) -> tuple[str, float]:
    """Run every hook at ``stage`` over the whole tree, capturing output.

    Args:
        stage: The pre-commit hook stage to run.
        skip: Comma-separated hook ids to skip, via pre-commit's SKIP env.

    Returns:
        The combined output and the wall-clock the whole run took.
    """
    env = dict(os.environ)
    if skip:
        env["SKIP"] = skip
    started = time.monotonic()
    completed = subprocess.run(
        [
            "uv",
            "run",
            "--frozen",
            "python",
            "-m",
            "pre_commit",
            "run",
            "--all-files",
            "--hook-stage",
            stage,
            "--verbose",
            "--color",
            "never",
        ],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        # Hook output is whatever the tools emit, which is not the Windows
        # ANSI code page: decoding under it raises mid-read and loses the
        # whole measurement, so decode permissively and keep the timings.
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=_RUN_TIMEOUT_SECONDS,
    )
    return completed.stdout + completed.stderr, time.monotonic() - started


def _render(timings: list[HookTiming], total: float | None) -> str:
    """Render the timing table, slowest hook first."""
    measured = [t for t in timings if not t.skipped]
    measured.sort(key=lambda t: t.seconds or 0.0, reverse=True)
    lines = [f"{'hook':<{_TABLE_WIDTH}} {'seconds':>9}", "-" * (_TABLE_WIDTH + 10)]
    for timing in measured:
        label = timing.hook_id
        if timing.runs > 1:
            label = f"{timing.hook_id}  (ran {timing.runs}x, argv-chunked)"
        lines.append(f"{label:<{_TABLE_WIDTH}} {timing.seconds:>9.2f}")
        ranked = sorted(timing.children.items(), key=lambda kv: kv[1], reverse=True)
        for name, seconds in ranked[:_SUB_TABLE_LIMIT]:
            lines.append(f"  {name:<{_TABLE_WIDTH - 2}} {seconds:>9.2f}")
    lines.append("-" * (_TABLE_WIDTH + 10))
    lines.append(
        f"{'sum of hook durations':<{_TABLE_WIDTH}} "
        f"{sum(t.seconds or 0.0 for t in measured):>9.2f}"
    )
    if total is not None:
        lines.append(f"{'wall-clock of the whole run':<{_TABLE_WIDTH}} {total:>9.2f}")
    skipped = [t.hook_id for t in timings if t.skipped]
    if skipped:
        lines.append(f"\nskipped ({len(skipped)}): {', '.join(sorted(skipped))}")
    return "\n".join(lines)


def _as_json(timings: list[HookTiming], total: float | None, stage: str) -> str:
    """Serialise the timings for before/after diffing."""
    payload = {
        "stage": stage,
        "wall_clock_seconds": total,
        "hooks": [
            {
                "hook_id": timing.hook_id,
                "seconds": timing.seconds,
                "runs": timing.runs,
                "children": timing.children,
            }
            for timing in timings
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(description=_DESCRIPTION)
    parser.add_argument("--stage", choices=_STAGES, default=_DEFAULT_STAGE)
    parser.add_argument(
        "--skip",
        default="",
        help="Comma-separated hook ids to skip (forwarded as pre-commit's SKIP).",
    )
    parser.add_argument("--json", dest="json_path", help="Write the timings here.")
    parser.add_argument(
        "--from-log",
        dest="from_log",
        help="Parse an existing verbose run's log instead of running the hooks.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Measure or re-read a pre-push run and report per-hook timings."""
    args = _parse_args(argv)
    if args.from_log:
        log = Path(args.from_log)
        if not log.is_file():
            print(f"no such log: {log}", file=sys.stderr)
            return 2
        text, total = log.read_text(encoding="utf-8", errors="replace"), None
    else:
        text, total = _run_pre_commit(args.stage, args.skip)
    timings = parse_timings(text)
    if not timings:
        print(
            "no per-hook durations found. pre-commit prints them only under"
            " --verbose; if you passed --from-log, the log must come from a"
            " verbose run.",
            file=sys.stderr,
        )
        return 2
    print(_render(timings, total))
    if args.json_path:
        Path(args.json_path).write_text(
            _as_json(timings, total, args.stage), encoding="utf-8"
        )
        print(f"\nwrote {args.json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
