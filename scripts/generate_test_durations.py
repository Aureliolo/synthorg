#!/usr/bin/env python3
"""Merge sharded pytest JUnit reports into a pytest-split durations file.

``pytest-split`` balances shards from a JSON map of ``nodeid -> seconds``.
Without one it partitions by test COUNT, which says nothing about cost: a
shard of ten thousand app-construction tests and a shard of ten thousand
pure-function tests get the same share and finish minutes apart.

The obvious way to record durations is ``pytest --store-durations``, but
that writes only the tests the running process saw, so under ``--splits``
each shard would overwrite the file with its own quarter. The JUnit XML
every shard already uploads carries a ``time`` per test, so merging those
reports reconstructs the whole suite from one CI run, at the runner's own
timings rather than a developer laptop's.

Usage::

    python scripts/generate_test_durations.py --out .test_durations.unit
        junit-unit-1.xml junit-unit-2.xml junit-unit-3.xml junit-unit-4.xml

JUnit records a dotted ``classname`` rather than a path, so the module
boundary is recovered by testing successively shorter prefixes against the
filesystem; anything after it is the class chain.
"""

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

# Below this, a test's own timing is noise next to per-test fixture and
# collection overhead, and storing it only inflates the file. pytest-split
# treats an absent entry as the average of the recorded ones, which is the
# right guess for a test in the noise band and lets the file stay small
# enough to track: the imbalance comes from the expensive tail, not from
# which shard holds a few thousand sub-decisecond tests.
_MIN_RECORDED_SECONDS: Final[float] = 0.05

# A report may name a test the tree no longer has: it was recorded before a
# rename, or against a branch with tests this one lacks. That is ordinary
# drift and the entry is simply dropped. A LOT of it is not drift, it is the
# classname-to-path resolution having broken, which would quietly write a
# near-empty file and leave the shards partitioning by count again.
_MAX_UNRESOLVED_FRACTION: Final[float] = 0.02


@dataclass(slots=True)
class MergedDurations:
    """Durations recovered from one or more JUnit reports.

    Attributes:
        durations: ``nodeid -> seconds`` for every test worth recording.
        seen: How many test cases the reports carried.
        unresolved_cases: How many of those named a module not on disk.
            Counted in cases rather than distinct classnames because the
            threshold is a share of the suite, and one missing class can
            account for hundreds of cases or for one.
        unresolved: The distinct classnames, for the error message.
    """

    durations: dict[str, float] = field(default_factory=dict)
    seen: int = 0
    unresolved_cases: int = 0
    unresolved: set[str] = field(default_factory=set)


def _module_and_classes(
    classname: str, repo_root: Path
) -> tuple[str, list[str]] | None:
    """Split a JUnit ``classname`` into its module path and class chain.

    Args:
        classname: Dotted name, e.g. ``tests.unit.api.test_app.TestCreateApp``.
        repo_root: Directory the module path is resolved against.

    Returns:
        The POSIX module path and the enclosing class names, outermost
        first (empty for a module-level test), or ``None`` when no prefix
        names a file on disk.
    """
    parts = classname.split(".")
    for cut in range(len(parts), 0, -1):
        candidate = Path(*parts[:cut]).with_suffix(".py")
        if (repo_root / candidate).is_file():
            return candidate.as_posix(), parts[cut:]
    return None


def durations_from_report(path: Path, repo_root: Path) -> MergedDurations:
    """Read one JUnit report into ``nodeid -> seconds``.

    Args:
        path: The JUnit XML file.
        repo_root: Directory module paths are resolved against.

    Returns:
        The per-test durations, plus what could not be resolved.

    Raises:
        ValueError: When the file cannot be parsed. A missing shard would
            drop a quarter of the suite back to count-based partitioning.
    """
    try:
        tree = ET.parse(path)  # noqa: S314 -- CI-generated report, not user input
    except (OSError, ET.ParseError) as exc:
        msg = f"cannot read JUnit report {path}: {exc}"
        raise ValueError(msg) from exc

    result = MergedDurations()
    for case in tree.iter("testcase"):
        classname = case.get("classname") or ""
        name = case.get("name") or ""
        if not classname or not name:
            continue
        result.seen += 1
        resolved = _module_and_classes(classname, repo_root)
        if resolved is None:
            result.unresolved_cases += 1
            result.unresolved.add(classname)
            continue
        module, classes = resolved
        seconds = float(case.get("time") or 0.0)
        if seconds < _MIN_RECORDED_SECONDS:
            continue
        nodeid = "::".join([module, *classes, name])
        # A parametrised test can appear once per shard only, so a repeat
        # is a genuine re-run; the slower reading is the safer estimate.
        result.durations[nodeid] = max(seconds, result.durations.get(nodeid, 0.0))
    return result


def merge_reports(paths: list[Path], repo_root: Path) -> MergedDurations:
    """Merge every report into one durations map.

    Args:
        paths: The JUnit XML files, one per shard.
        repo_root: Directory module paths are resolved against.

    Returns:
        The merged durations, sorted by nodeid so the file diffs cleanly.

    Raises:
        ValueError: When a report is unreadable, the merge is empty, or too
            much of it failed to resolve to a module.
    """
    merged = MergedDurations()
    for path in paths:
        report = durations_from_report(path, repo_root)
        merged.seen += report.seen
        merged.unresolved_cases += report.unresolved_cases
        merged.unresolved |= report.unresolved
        for nodeid, seconds in report.durations.items():
            merged.durations[nodeid] = max(seconds, merged.durations.get(nodeid, 0.0))
    if not merged.durations:
        msg = "merged durations are empty; the reports recorded no tests"
        raise ValueError(msg)
    unresolved_share = merged.unresolved_cases / max(merged.seen, 1)
    if unresolved_share > _MAX_UNRESOLVED_FRACTION:
        sample = ", ".join(sorted(merged.unresolved)[:5])
        msg = (
            f"{merged.unresolved_cases} of {merged.seen} test cases resolved to no "
            f"module ({unresolved_share:.1%}); this is a broken path resolution, "
            f"not test drift. First few: {sample}"
        )
        raise ValueError(msg)
    merged.durations = dict(sorted(merged.durations.items()))
    return merged


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        ``0`` when the durations file was written.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)

    try:
        merged = merge_reports(args.reports, args.repo_root.resolve())
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    args.out.write_text(json.dumps(merged.durations, indent=2) + "\n", encoding="utf-8")
    total = sum(merged.durations.values())
    print(
        f"OK: {len(merged.durations)} timed tests, {total:.0f}s total, "
        f"{len(merged.unresolved)} classname(s) not on disk, written to {args.out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
