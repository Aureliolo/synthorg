#!/usr/bin/env python3
"""Pre-commit gate: block new shell ``git commit + push`` in workflows.

Any workflow that writes a commit to the repository must go through the
Git Data REST API (``POST /git/commits`` + ``PATCH /git/refs/...``)
authenticated by the ``synthorg-repo-bot`` App installation token.
Shell ``git commit`` + ``git push`` from the runner never produces a
GitHub-signed commit, regardless of which token it uses:

1. Locally-built commits + push with ``GITHUB_TOKEN`` are signed by
   GitHub under ``github-actions[bot]`` -- but those pushes are
   suppressed from firing downstream workflow events (GitHub's
   anti-recursion rule). This is the exact failure mode
   ``auto-rollover.yml`` was designed to avoid.
2. Locally-built commits + push with **any non-GITHUB_TOKEN credential**
   (PAT, App installation token, resurrected ``RELEASE_PLEASE_TOKEN``)
   produce **unsigned** commits. GitHub can only attach a bot signature
   when a commit is created through the Git Data API; it does not
   interpose on ``git push``. See
   https://docs.github.com/en/authentication/managing-commit-signature-verification/about-commit-signature-verification
   ("signature verification for bots ... will only work if the request
   is verified and authenticated as the GitHub App or bot and contains
   no custom author information ... and no custom signature information,
   such as Commits API"). ``main`` rejects unsigned commits via
   ``required_signatures``, so any shell ``git commit + git push`` on
   ``main`` silently fails the branch-protection gate.

This gate flags every ``run:`` block containing both ``git commit`` and
``git push``. There is NO whitelist for "job mints an App token
somewhere" -- that whitelist was the original shape but it is unsound:
the token mint buys API-path signing, not local-git-push signing, so
the presence of a mint does not sanitise a local-git write. Workflows
that need to write to the repo must invoke the Git Data API directly
(see ``auto-rollover.yml`` / ``graduate.yml`` / ``dev-release.yml`` for
reference implementations).

Baseline
--------

``scripts/_workflow_shell_git_commits_baseline.json`` records any
grandfathered sites as ``[file, job_name, step_name]`` triples. The
goal state after #1555 is an empty baseline; any future addition goes
through the same shrink-only discipline as
``check_logger_exception_str_exc.py`` -- additions require
``--refresh-baseline --force`` and an explicit audit trail.

Usage::

    python scripts/check_workflow_shell_git_commits.py <file>...   # pre-commit
    python scripts/check_workflow_shell_git_commits.py --scan-all  # CI
    python scripts/check_workflow_shell_git_commits.py --refresh-baseline
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from collections.abc import Iterable

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS_ROOT = _REPO_ROOT / ".github" / "workflows"
_BASELINE_PATH = (
    Path(__file__).resolve().parent / "_workflow_shell_git_commits_baseline.json"
)

# Match ``git commit`` / ``git push`` anywhere the shell would execute
# them -- not just at the start of a line. A ``foo && git commit``
# chain writes an unsigned commit the same way a standalone invocation
# does, and a shared-job subshell trick like ``(cd tmp && git commit
# -am .)`` would slip past a line-start anchor. The word boundary
# guards against false positives on strings like ``git-commitlint`` or
# a YAML key named ``git_commit``.
_GIT_COMMIT_RE = re.compile(r"\bgit\s+commit\b")
_GIT_PUSH_RE = re.compile(r"\bgit\s+push\b")
# Strip FULL-LINE shell comments (whole line is a ``# ...`` comment)
# before scanning, so a documented ``# git commit ...`` example in a
# ``run:`` block does not trip the gate. The regex is deliberately
# anchored to the start of a line (``^\s*#``) -- an in-line ``#`` is
# left alone because it is almost always part of a quoted string
# (``git commit -m "fix: #1555"``), not a comment. Stripping in-line
# hashes would eat real command chains like
# ``git commit -m "fix: #1555" && git push`` and let the push slip
# past the detector.
_SHELL_COMMENT_RE = re.compile(r"(?m)^\s*#[^\n]*$")

# Each baseline list entry is ``[job_id, step_key]`` -- a length-2
# list. Named so the `len(entry) == _BASELINE_ENTRY_LEN` check stays
# readable without a literal 2.
_BASELINE_ENTRY_LEN = 2

_STEERING_MESSAGE = (
    "Shell `git commit` + `git push` inside a workflow NEVER produces a "
    "GitHub-signed commit -- GitHub can only attach a bot signature when "
    "the commit is created through the Git Data API (POST /git/commits). "
    "Route writes through the API with an App installation token. "
    "Reference implementations: auto-rollover.yml, graduate.yml, "
    "dev-release.yml."
)

_FORCE_REFRESH_FLAG = "--force"


def _iter_workflow_files() -> Iterable[Path]:
    """Walk ``.github/workflows/`` for YAML files."""
    if not _WORKFLOWS_ROOT.exists():
        return
    for pattern in ("*.yml", "*.yaml"):
        yield from sorted(_WORKFLOWS_ROOT.rglob(pattern))


def _scan_file(path: Path) -> list[tuple[str, str]]:
    """Return ``(job_id, step_key)`` tuples for each unsafe step.

    ``step_key`` is ``step.name`` if present, else ``step-index-N`` so we
    always have a stable identifier.

    A step is flagged when a ``git push`` anywhere in the JOB is
    preceded by a ``git commit`` in the same job -- either in the same
    ``run:`` block, or in ANY earlier step. Splitting commit + push
    across steps does not launder the unsigned-commit property: the
    commit object created in step N is the one step N+1 pushes, still
    unsigned because GitHub only signs commits written via the Git
    Data API.

    There is no whitelist based on tokens minted elsewhere in the
    job: a local ``git push`` produces unsigned commits regardless of
    which credential the push uses. Workflows that need to write to
    the repo must invoke the Git Data API directly.

    Raises on YAML parse errors so callers surface them instead of a
    false-green "no violations".
    """
    source = path.read_text(encoding="utf-8")
    data = yaml.safe_load(source)
    if not isinstance(data, dict):
        return []
    jobs = data.get("jobs") or {}
    hits: list[tuple[str, str]] = []
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            continue
        steps = job.get("steps") or []
        commit_seen = False
        commit_step_key: str | None = None
        for idx, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            run_block = step.get("run")
            if not isinstance(run_block, str):
                continue
            # Strip full-line shell comments before the regex so
            # documented examples (``# see: git commit ...``) do not
            # trip the gate.
            scrubbed = _SHELL_COMMENT_RE.sub("", run_block)
            step_key = step.get("name") or f"step-index-{idx}"

            has_commit = bool(_GIT_COMMIT_RE.search(scrubbed))
            has_push = bool(_GIT_PUSH_RE.search(scrubbed))

            if has_commit and has_push:
                # Same-step violation: commit + push in one run block.
                hits.append((str(job_id), str(step_key)))
                # Reset tracking -- this step is already flagged; any
                # later push in the same job without a fresh intervening
                # commit would duplicate-report.
                commit_seen = False
                commit_step_key = None
                continue
            if has_push and commit_seen:
                # Split-step violation: a prior step in this job
                # committed, this step pushes. Flag the push step:
                # the push is the visible side-effect, and flagging
                # both would be noisy.
                hits.append((str(job_id), str(step_key)))
                commit_seen = False
                commit_step_key = None
                continue
            if has_commit:
                commit_seen = True
                commit_step_key = str(step_key)

        # If a job commits but never pushes, that is not the signed-
        # commit-to-main failure mode this gate exists to catch (no
        # push = no ref update = no unsigned-commit landing on main).
        # Intentionally do not flag.
        _ = commit_step_key  # reserved for future richer reporting.

    return sorted(hits)


def _rel(path: Path) -> str:
    """Repo-relative POSIX path for stable baseline keys."""
    return path.resolve().relative_to(_REPO_ROOT).as_posix()


def _load_baseline() -> dict[str, set[tuple[str, str]]]:
    """Load the baseline file into a ``{path: {(job, step)}}`` map.

    The on-disk format is ``{"locations": {<rel-path>: [[job, step], ...]}}``
    plus metadata keys. Missing / malformed baselines read as empty so the
    gate fails closed.
    """
    if not _BASELINE_PATH.exists():
        return {}
    with _BASELINE_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    locations = data.get("locations", {})
    if not isinstance(locations, dict):
        return {}
    result: dict[str, set[tuple[str, str]]] = {}
    for key, entries in locations.items():
        if not isinstance(entries, list):
            continue
        result[str(key)] = {
            (str(entry[0]), str(entry[1]))
            for entry in entries
            if isinstance(entry, list) and len(entry) == _BASELINE_ENTRY_LEN
        }
    return result


def _current_commit_sha() -> str:
    """Return short HEAD SHA, or ``unknown`` if git is unavailable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=str(_REPO_ROOT),
        )
    except subprocess.CalledProcessError, FileNotFoundError, OSError:
        return "unknown"
    return result.stdout.strip() or "unknown"


def _save_baseline(locations: dict[str, list[tuple[str, str]]]) -> None:
    """Write a sorted, metadata-tagged baseline snapshot."""
    sorted_locations = {
        key: [[job, step] for job, step in sorted(entries)]
        for key, entries in sorted(locations.items())
    }
    payload = {
        "description": (
            "#1555 baseline: list of workflow steps that run shell "
            "`git commit + git push` without an accompanying App-token "
            "mint. Generated by "
            "scripts/check_workflow_shell_git_commits.py --refresh-baseline. "
            "Do not hand-edit."
        ),
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": _current_commit_sha(),
        "remediation_issue": "https://github.com/Aureliolo/synthorg/issues/1555",
        "locations": sorted_locations,
    }
    # ``newline=""`` keeps Windows runners from translating ``\n`` into
    # ``\r\n`` when the final trailing newline is written below -- any
    # other value (including the default ``None``) would make the baseline
    # file diff noisily between contributors on different platforms.
    with _BASELINE_PATH.open("w", encoding="utf-8", newline="") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")


def _baseline_additions(
    previous: dict[str, set[tuple[str, str]]],
    current: dict[str, list[tuple[str, str]]],
) -> list[str]:
    """Return printable lines for every new ``(job, step)`` not in *previous*."""
    lines: list[str] = []
    for key, entries in sorted(current.items()):
        allowed = previous.get(key, set())
        for job, step in entries:
            if (job, step) not in allowed:
                lines.append(
                    f"refresh-would-add: {key}: job={job} step={step}: "
                    "shell git commit + push without App-token mint"
                )
    return lines


def cmd_refresh_baseline(*, force: bool = False) -> int:
    """Recompute the baseline; shrink-only unless ``--force``."""
    previous = _load_baseline()
    locations: dict[str, list[tuple[str, str]]] = {}
    for path in _iter_workflow_files():
        try:
            hits = _scan_file(path)
        except yaml.YAMLError as exc:
            print(f"{_rel(path)}: YAML parse error: {exc}", file=sys.stderr)
            return 1
        if hits:
            locations[_rel(path)] = list(hits)

    additions = _baseline_additions(previous, locations)
    if additions and not force:
        for line in additions:
            print(line, file=sys.stderr)
        print(
            "\nBaseline refresh would add the sites above. Convert them to "
            "use the release-runner-setup composite (or mint an App token "
            f"directly) first, or re-run with `{_FORCE_REFRESH_FLAG}` if "
            "expansion has been explicitly reviewed.",
            file=sys.stderr,
        )
        return 1
    _save_baseline(locations)
    total = sum(len(v) for v in locations.values())
    print(
        f"Baseline refreshed: {total} sites across {len(locations)} files "
        f"-> {_BASELINE_PATH.relative_to(_REPO_ROOT).as_posix()}",
    )
    return 0


def _scan_and_compare(
    path: Path, baseline: dict[str, set[tuple[str, str]]]
) -> list[str]:
    """Return violation lines for *path* versus its baseline entry."""
    try:
        hits = _scan_file(path)
    except yaml.YAMLError as exc:
        return [f"{_rel(path)}: YAML parse error: {exc}"]
    if not hits:
        return []
    key = _rel(path)
    allowed = baseline.get(key, set())
    return [
        f"{key}: job={job} step={step}: new shell git commit+push site"
        for job, step in hits
        if (job, step) not in allowed
    ]


def _report(violations: list[str]) -> int:
    """Print violations and the steering message."""
    if not violations:
        return 0
    for line in violations:
        print(line)
    print(f"\n{_STEERING_MESSAGE}", file=sys.stderr)
    return 1


def cmd_scan_all() -> int:
    """Scan every workflow file against the baseline."""
    baseline = _load_baseline()
    violations: list[str] = []
    for path in _iter_workflow_files():
        violations.extend(_scan_and_compare(path, baseline))
    return _report(violations)


def cmd_scan_paths(paths: Iterable[str]) -> int:
    """Scan the provided files only -- pre-commit entry point."""
    baseline = _load_baseline()
    violations: list[str] = []
    for p in paths:
        path = Path(p).resolve()
        if not path.exists() or path.suffix not in (".yml", ".yaml"):
            continue
        if not path.is_relative_to(_WORKFLOWS_ROOT):
            continue
        violations.extend(_scan_and_compare(path, baseline))
    return _report(violations)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Block new shell git commit+push sites in workflow files.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Files to check (pre-commit supplies these).",
    )
    parser.add_argument(
        "--scan-all",
        action="store_true",
        help="Scan every workflow file (CI mode).",
    )
    parser.add_argument(
        "--refresh-baseline",
        action="store_true",
        help=(
            "Rewrite _workflow_shell_git_commits_baseline.json from the current state."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Allow --refresh-baseline to add new locations. Required on "
            "the initial capture or any explicitly-reviewed expansion; "
            "without this flag, refresh only permits baseline shrinkage."
        ),
    )
    args = parser.parse_args(argv)

    if args.refresh_baseline:
        return cmd_refresh_baseline(force=args.force)
    if args.scan_all:
        return cmd_scan_all()
    return cmd_scan_paths(args.paths)


if __name__ == "__main__":
    sys.exit(main())
