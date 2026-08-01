#!/usr/bin/env python3
r"""Compute SynthOrg release cadence metrics.

Writes a markdown report to ``docs/reference/release-cadence.md`` with:

* Days between consecutive stable releases (count + mean + median + p90).
* Time from each feature PR's merge to the first stable release that
  included it (p50 + p90 + count).

The script runs via ``uv run python scripts/compute_release_cadence.py``
locally, or from a CI job that refreshes the committed Markdown on
push-to-main. Output carries a "do not edit by hand" header so drift
between committed state and regenerated state is visually obvious.

Inputs: stable tags ``^v\d+\.\d+\.\d+$`` (no pre-release suffix) in the
local clone; no network calls. GitHub tag creation dates come from
``git for-each-ref --sort=creatordate``.

A clean clone with ``fetch-depth: 0`` is required; otherwise tag
history is incomplete and medians skew.
"""

import itertools
import re
import statistics
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_OUTPUT = _REPO_ROOT / "docs" / "reference" / "release-cadence.md"
_STABLE_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")

# `git for-each-ref --format=a\tb` yields at least 2 fields per ref when
# creatordate is present. We also accept 3 fields for the creatordate +
# authordate fallback pair, so ``len(parts) < _MIN_REF_FIELDS`` skips
# malformed lines without hardcoding a literal 2.
_MIN_REF_FIELDS = 2
_REF_WITH_FALLBACK_FIELDS = 3
# `git log --format=%H\t%cI\t%s` yields exactly 3 fields per commit.
# We prefer committer date (%cI) over author date (%aI) because the
# "merge to stable release" metric measures how long a commit waits on
# `main` before shipping -- author date is "when the branch commit was
# written", which can predate the merge by weeks and distort the p50.
_LOG_EXPECTED_FIELDS = 3
_SECONDS_PER_DAY = 86400.0


@dataclass(frozen=True)
class StableTag:
    """A stable release tag with its creation timestamp."""

    name: str
    created_at: datetime


def _run_git(*args: str) -> str:
    """Run ``git <args>`` and return stripped stdout. Raises on non-zero."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _load_stable_tags() -> list[StableTag]:
    """Return stable-tag snapshots sorted by creation timestamp ascending.

    Uses ``creatordate`` so annotated-tag object time is preferred; falls
    back to the tag-pointed commit's authordate otherwise. ISO-8601 is
    parsed into timezone-aware datetimes.
    """
    output = _run_git(
        "for-each-ref",
        "--format=%(refname:short)\t%(creatordate:iso-strict)\t%(authordate:iso-strict)",
        "--sort=creatordate",
        "refs/tags/",
    )
    tags: list[StableTag] = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < _MIN_REF_FIELDS:
            continue
        name = parts[0]
        if not _STABLE_TAG_RE.match(name):
            continue
        ts_str = parts[1] or (
            parts[2] if len(parts) >= _REF_WITH_FALLBACK_FIELDS else ""
        )
        if not ts_str:
            continue
        created_at = datetime.fromisoformat(ts_str)
        # `creatordate:iso-strict` always emits a tz offset, but the
        # `authordate` fallback can theoretically produce a naive value
        # on ancient commits with no committer timezone. Normalise to
        # UTC so downstream subtraction against other tz-aware datetimes
        # never raises TypeError on mixed-awareness operands.
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        tags.append(StableTag(name=name, created_at=created_at))
    return tags


def _days_between(tags: list[StableTag]) -> list[float]:
    """Return the gap (in days) between each consecutive pair of tags."""
    return [
        (curr.created_at - prev.created_at).total_seconds() / _SECONDS_PER_DAY
        for prev, curr in itertools.pairwise(tags)
    ]


def _percentile(values: list[float], pct: float) -> float:
    """Inclusive-percentile helper (returns 0 on empty input)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = (pct / 100.0) * (len(ordered) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def _is_feat_commit(subject: str) -> bool:
    """Return True for conventional-commit feat-family subjects.

    Matches ``feat:``, ``feat(scope):``, and the breaking-feature form
    ``feat!:`` / ``feat(scope)!:``. Anchoring on the colon / opening
    parenthesis / bang avoids matching unrelated prefixes such as
    ``feature:`` or ``feature-branch-cleanup`` that share the leading
    ``feat`` substring.
    """
    return subject.startswith(("feat:", "feat(", "feat!:"))


def _feat_merge_to_release_deltas(tags: list[StableTag]) -> list[float]:
    """Return (merge, release) age deltas in days for each ``feat:`` commit.

    For each stable tag, finds every ``feat:`` commit reachable from the
    tag that is not reachable from the previous stable tag, and measures
    the age from the commit's committer date to the tag's ``created_at``.
    The first tag in the list is walked separately (with no lower bound)
    so features shipped in the initial release are included -- pairwise
    iteration would silently drop them.
    """
    deltas: list[float] = []
    prev_name: str | None = None
    for curr in tags:
        log_range = f"{prev_name}..{curr.name}" if prev_name else curr.name
        revlist = _run_git(
            "log",
            log_range,
            "--format=%H\t%cI\t%s",
        )
        prev_name = curr.name
        for line in revlist.splitlines():
            parts = line.split("\t", 2)
            if len(parts) < _LOG_EXPECTED_FIELDS:
                continue
            committer_iso, subject = parts[1], parts[2]
            if not _is_feat_commit(subject):
                continue
            committer_dt = datetime.fromisoformat(committer_iso)
            if committer_dt.tzinfo is None:
                committer_dt = committer_dt.replace(tzinfo=UTC)
            # Clamp to zero in case a tag's creation timestamp sits a few
            # seconds behind a commit's committer date due to clock drift
            # between the runner that created the tag and the runner that
            # committed the feature. Negative deltas would skew the mean.
            delta_seconds = max(0.0, (curr.created_at - committer_dt).total_seconds())
            deltas.append(delta_seconds / _SECONDS_PER_DAY)
    return deltas


def _format_summary(label: str, values: list[float], unit: str = "days") -> str:
    """Render a markdown row summarising *values* with count / mean / median / p90."""
    if not values:
        return f"- **{label}**: no data"
    return (
        f"- **{label}**: n={len(values)}"
        f", mean={statistics.fmean(values):.1f} {unit}"
        f", median={statistics.median(values):.1f} {unit}"
        f", p90={_percentile(values, 90):.1f} {unit}"
    )


def _render(tags: list[StableTag]) -> str:
    """Render the markdown body.

    Output is deterministic for a given tag history: no wall-clock
    timestamp is embedded, so re-running the script against an
    unchanged set of tags produces a byte-identical file. This keeps
    the committed snapshot diff-free when no new releases have landed,
    so regeneration never churns PRs. When you actually need to know
    *when* the snapshot was last refreshed, consult ``git log`` on
    ``docs/reference/release-cadence.md``.
    """
    gaps = _days_between(tags)
    feat_deltas = _feat_merge_to_release_deltas(tags)

    lines = [
        "---",
        "title: Release cadence",
        "---",
        "",
        "<!--",
        "Generated by scripts/compute_release_cadence.py. Do not edit by hand.",
        "-->",
        "",
        "# Release cadence",
        "",
        "Tracks how quickly features flow from merge to stable release and how",
        "often new stable releases ship. Generated by",
        "`scripts/compute_release_cadence.py` against the local tag history and",
        "committed as a static snapshot -- the committed file is what docs and",
        "readers see. Refresh by running the script (or its CI job in",
        "`build-docs.yml`, which re-runs it on every push to `main`) and committing",
        "the result.",
        "",
        "The `merge to stable release` delta uses each commit's **committer**",
        "date (`%cI`), not its author date, so the metric reflects when the",
        "squash-merge landed on `main` rather than when the feature branch was",
        "first written.",
        "",
        "## Summary",
        "",
        _format_summary("Days between consecutive stable releases", gaps),
        _format_summary(
            "Feature merge to stable release (feat: commits only)",
            feat_deltas,
        ),
        "",
        "## Stable releases",
        "",
        "| Tag | Created (UTC) |",
        "| --- | --- |",
    ]
    lines.extend(
        f"| `{tag.name}` | "
        f"{tag.created_at.astimezone(UTC).strftime('%Y-%m-%d %H:%M:%S')} |"
        for tag in tags
    )
    lines.append("")
    return "\n".join(lines)


def _assert_full_history() -> None:
    """Abort if the working tree is a shallow clone.

    Cadence metrics depend on the full tag history AND the full commit
    reach from each tag back to its predecessor. A shallow clone
    (``fetch-depth: 1`` on CI, or a user-initiated
    ``git clone --depth N``) silently trims older tags and turns
    ``git rev-list`` results into partial ranges, which makes every
    metric wrong without any overt error. Fail loud so CI / local
    runs in that state produce an actionable message instead of a
    plausibly-wrong committed file.
    """
    result = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    is_shallow = (result.stdout or "").strip().lower() == "true"
    if is_shallow:
        print(
            "error: repository is a shallow clone -- cadence metrics would be "
            "incorrect because older tags and/or commits are missing.\n"
            "Re-run after fetching the full history: `git fetch --unshallow` "
            "locally, or `actions/checkout` with `fetch-depth: 0` in CI.",
            file=sys.stderr,
        )
        sys.exit(2)


def main() -> int:
    """CLI entry point."""
    _assert_full_history()
    tags = _load_stable_tags()
    if not tags:
        print("No stable tags found; cannot compute cadence", file=sys.stderr)
        return 1
    body = _render(tags)
    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT.write_text(body, encoding="utf-8", newline="\n")
    print(f"Wrote {_OUTPUT.relative_to(_REPO_ROOT)} ({len(tags)} stable tags)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
