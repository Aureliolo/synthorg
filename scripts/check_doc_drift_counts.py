#!/usr/bin/env python3
"""Gate precise event-module floor claims against codebase reality."""

import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EVENT_MODULES_DIR = REPO_ROOT / "src" / "synthorg" / "observability" / "events"


@dataclass(frozen=True)
class Claim:
    """A single floor claim registered against a docs file."""

    path: str
    pattern: re.Pattern[str]
    label: str


# Marketing-style counts (eg "25,000+ unit tests" in README/roadmap) are
# deliberately NOT registered here. They are rounded narrative numbers
# under human editorial control. Only precise technical claims
# (numbers a reader will cross-reference against code) are gated.
# Patterns accept thousands separators in the captured group so a future
# floor like "1,000+" still matches; parse_claim strips the comma before
# converting to int.
CLAIMS: tuple[Claim, ...] = (
    Claim(
        "docs/design/observability.md",
        re.compile(r"([\d,]+)\+ domain-specific event constant modules"),
        label="observability event modules",
    ),
    Claim(
        "docs/design/agent-execution.md",
        re.compile(r"\(([\d,]+)\+ constants\)"),
        label="agent-execution event constants",
    ),
    Claim(
        "docs/research/control-plane-audit.md",
        re.compile(r"([\d,]+)\+ event constant modules"),
        label="control-plane-audit event modules",
    ),
    Claim(
        "docs/research/control-plane-audit.md",
        re.compile(r"([\d,]+)\+ structured event constants"),
        label="control-plane-audit structured event constants",
    ),
    Claim(
        "docs/research/control-plane-audit.md",
        re.compile(r"observability \(([\d,]+)\+ structured events\)"),
        label="control-plane-audit observability events",
    ),
    Claim(
        "docs/research/acg-formalism-evaluation.md",
        re.compile(r"([\d,]+)\+ event constant domains"),
        label="acg-formalism event domains",
    ),
)


def count_event_modules() -> int:
    """Return the number of domain event-constant modules."""
    return sum(
        1 for path in EVENT_MODULES_DIR.glob("*.py") if path.name != "__init__.py"
    )


def parse_claim(claim: Claim) -> int:
    """Read the file, run the pattern, and return the parsed integer floor."""
    resolved = (REPO_ROOT / claim.path).resolve()
    if not resolved.is_relative_to(REPO_ROOT):
        msg = f"Claim path escapes REPO_ROOT: {claim.path!r}"
        raise RuntimeError(msg)
    try:
        text = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        # Re-raise as RuntimeError so main()'s gate handler prints a
        # concise single-line message instead of a stacktrace.
        msg = f"Could not read claim file {claim.path}: {type(exc).__name__}: {exc}"
        raise RuntimeError(msg) from exc
    match = claim.pattern.search(text)
    if not match:
        msg = (
            f"Could not find claim pattern in {claim.path}:"
            f" {claim.pattern.pattern!r}.\n"
            "If the claim was rewritten, update CLAIMS in"
            " scripts/check_doc_drift_counts.py."
        )
        raise RuntimeError(msg)
    return int(match.group(1).replace(",", ""))


def main() -> int:
    """Verify every registered claim's floor is at or below actual."""
    if not EVENT_MODULES_DIR.is_dir():
        print(
            f"Error: event modules directory not found: {EVENT_MODULES_DIR}."
            " Verify the path or refactor reference in"
            " scripts/check_doc_drift_counts.py.",
            file=sys.stderr,
        )
        return 1

    actual = count_event_modules()

    failures: list[str] = []
    for claim in CLAIMS:
        try:
            floor = parse_claim(claim)
        except RuntimeError as exc:
            print(f"Doc drift gate error: {exc}", file=sys.stderr)
            return 1
        if actual < floor:
            failures.append(
                f"  {claim.label} ({claim.path}): claim '{floor:,}+' but"
                f" actual is {actual:,}. Lower the floor or investigate."
            )

    if failures:
        print("Doc count drift detected:", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        print(
            "\nThe doc value is the lower bound; the codebase has fallen"
            " below it. Adjust the floor in the affected docs (or restore"
            " the deleted tests / event modules).",
            file=sys.stderr,
        )
        return 1

    print(
        f"OK: all {len(CLAIMS)} claims satisfy floor <= actual"
        f" (event_modules={actual})."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
