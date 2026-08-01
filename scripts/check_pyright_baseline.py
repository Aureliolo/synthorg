#!/usr/bin/env python3
"""Gate: pyright findings may shrink, never grow.

Mypy is authoritative; pyright's narrowing, generics and overload analysis
disagrees with it in places, so it cannot be flipped to zero findings
without breaking every push.

Per-rule rather than one total, because a bare count lets a new
``reportOptionalMemberAccess`` land as long as a ``reportArgumentType``
was deleted in the same PR. Not per-file or per-line: findings move
whenever code moves, so a location-keyed baseline would fail on a pure
rename while saying nothing about type safety.

Baseline only shrinks; growth needs ``ALLOW_BASELINE_GROWTH=1``. Creating it
for the first time is exempt, since with no file on disk every rule reads as
new and the baseline could otherwise never be seeded.

Usage::

    uv run pyright --outputjson > pyright.json
    uv run python scripts/check_pyright_baseline.py --report pyright.json
"""

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Final

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_BASELINE_PATH: Final[Path] = _REPO_ROOT / "scripts" / "pyright_finding_baseline.json"
_NO_RULE: Final[str] = "(no-rule)"
_GROWTH_ENV: Final[str] = "ALLOW_BASELINE_GROWTH"


def _load_counts(report_path: Path) -> Counter[str]:
    """Count pyright errors per rule from a ``--outputjson`` report."""
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    counts: Counter[str] = Counter()
    for diagnostic in payload.get("generalDiagnostics", []):
        if diagnostic.get("severity") != "error":
            continue
        counts[str(diagnostic.get("rule") or _NO_RULE)] += 1
    return counts


def _load_baseline() -> dict[str, int]:
    """Read the committed baseline, or an empty mapping if absent."""
    if not _BASELINE_PATH.exists():
        return {}
    loaded = json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))
    return {str(rule): int(count) for rule, count in loaded.items()}


def _write_baseline(counts: Counter[str]) -> None:
    """Persist the current counts as the new baseline."""
    ordered = dict(sorted(counts.items()))
    _BASELINE_PATH.write_text(
        json.dumps(ordered, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _violations(counts: Counter[str], baseline: dict[str, int]) -> list[str]:
    """Report every rule whose count exceeds its baseline."""
    problems: list[str] = []
    for rule in sorted(set(counts) | set(baseline)):
        allowed = baseline.get(rule, 0)
        actual = counts.get(rule, 0)
        if actual <= allowed:
            continue
        if rule in baseline:
            problems.append(f"{rule}: {actual} findings, baseline allows {allowed}")
        else:
            problems.append(
                f"{rule}: {actual} findings, this rule is NOT in the baseline "
                "(a new category of type error)"
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    """Compare a pyright report against the shrink-only baseline."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report",
        type=Path,
        required=True,
        help="Path to `pyright --outputjson` output.",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Rewrite the baseline from this report (shrink only, unless "
        f"{_GROWTH_ENV}=1).",
    )
    args = parser.parse_args(argv)

    if not args.report.exists():
        print(f"::error::pyright report not found: {args.report}", file=sys.stderr)
        return 2

    counts = _load_counts(args.report)
    # Seeding the first baseline is initialisation, not widening: with no file
    # on disk every rule reads as new, so growth protection would make the
    # baseline impossible to create. It applies from the second write onward.
    seeding = not _BASELINE_PATH.exists()
    baseline = _load_baseline()
    problems = _violations(counts, baseline)
    growth_allowed = os.environ.get(_GROWTH_ENV) == "1"

    if args.update_baseline:
        if problems and not seeding and not growth_allowed:
            print(
                "::error::refusing to grow the pyright baseline. Fix the new "
                f"findings, or set {_GROWTH_ENV}=1 with explicit approval.",
                file=sys.stderr,
            )
            for problem in problems:
                print(f"  {problem}", file=sys.stderr)
            return 1
        _write_baseline(counts)
        action = "seeded" if seeding else "updated"
        print(
            f"baseline {action}: {sum(counts.values())} finding(s) "
            f"across {len(counts)} rule(s)"
        )
        return 0

    if problems:
        print("::error::pyright findings grew past the baseline:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print(
            "\nFix the new findings. If a finding is a genuine pyright/mypy "
            "disagreement rather than a defect, narrow the type so both agree, "
            "or raise the baseline with explicit approval "
            f"({_GROWTH_ENV}=1 ... --update-baseline).",
            file=sys.stderr,
        )
        return 1

    total = sum(counts.values())
    allowed = sum(baseline.values())
    print(f"pyright: {total} finding(s), baseline allows {allowed}. OK.")
    if total < allowed:
        print(
            "Findings dropped below the baseline. Run with --update-baseline "
            "to ratchet it down."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
