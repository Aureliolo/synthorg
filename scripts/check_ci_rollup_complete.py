#!/usr/bin/env python3
"""Gate: a sentinel rollup must cover every job in its workflow.

Branch protection requires rollup contexts (``CI Pass``, ``CLI Pass``,
``Docker Pass``, ...) rather than each job, so a rollup gates only what its
``needs:`` names: add a job and forget to wire it in, and it runs, goes
red, and the required check stays green.

Five properties:

1. Every top-level job appears in some rollup's ``needs``, or sits in
   :data:`_EXEMPT` with a justification.
2. Each rollup's hand-written ``RESULTS`` env block names exactly what that
   rollup needs. In ``needs`` but not ``RESULTS`` waits without gating; the
   reverse expands to an empty string.
3. Every context ``branch_protection.yml`` requires is produced by some job.
   GitHub treats a never-reported required context as unsatisfied, blocking
   every PR permanently, so this is what makes renaming workflows safe.
4. No rollup-producing workflow carries a top-level ``paths:`` filter. The
   spec states this invariant and names this gate as its enforcement; a
   filtered workflow simply does not run on an unrelated PR, and property 3's
   never-reported-context deadlock follows.
5. ``release-cut.yml`` posts every required context on the release PR. That
   PR's head runs no workflow of its own, so each required context arrives as
   a hand-written commit status; one missing leaves the release PR wedged at
   "Expected, waiting".

:data:`_ROLLUPS` covers every workflow that produces a required context, so
property 1 holds wherever branch protection actually gates. A workflow left
out would keep property 3 but lose properties 1 and 2, which is the bug this
gate exists to catch.

No accumulating baseline: :data:`_EXEMPT` is a fixed set of reviewed
structural exceptions in source, not a regenerated ledger, and there is no
``--update-baseline`` flag to grow it.

Usage::

    uv run python scripts/check_ci_rollup_complete.py
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Final

import yaml

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_WORKFLOW_DIR: Final[str] = ".github/workflows"
_BRANCH_PROTECTION: Final[str] = ".github/branch_protection.yml"

# GitHub resolves workflow files under either extension, so a gate that walks
# only one of them measures a subset of what actually runs.
_WORKFLOW_GLOBS: Final[tuple[str, ...]] = ("*.yml", "*.yaml")

# Anchored at the fragment start so only a complete ``needs.<id>.result``
# matches. A bare ``".result" in fragment`` substring test also accepted
# ``needs.changes.outputs.result``, yielding the job id ``changes.outputs``,
# which is in no ``needs`` list and so raised a false violation that blocked
# every PR. An output legitimately named ``result`` or ``results`` is ordinary.
_RESULT_REF: Final[re.Pattern[str]] = re.compile(r"^([A-Za-z0-9_-]+)\.result\b")

# The release PR carries no workflow runs of its own (verify-backend.yml skips
# release-please heads), so release-cut.yml posts each required context as a
# commit status instead. A context added to the spec but not there leaves the
# release PR permanently "Expected, waiting".
_RELEASE_CUT: Final[str] = "release-cut.yml"
_STATUS_STEP_NAME: Final[str] = "Post required-check statuses on release PR"
_CONTEXT_LOOP: Final[re.Pattern[str]] = re.compile(
    r"for context in(.*?);\s*do", re.DOTALL
)
_QUOTED: Final[re.Pattern[str]] = re.compile(r'"([^"]+)"')

# workflow filename -> the job ids of its sentinel rollups.
_ROLLUPS: Final[dict[str, tuple[str, ...]]] = {
    "verify-backend.yml": ("ci-pass",),
    "verify-cli.yml": ("cli-pass",),
    "build-images.yml": ("docker-pass",),
    "perf-benchmarks.yml": ("codspeed-python-pass", "codspeed-web-pass"),
    "perf-web-vitals.yml": ("lighthouse-pass",),
}

# `<workflow>::<job>` -> why the rollup does not depend on it. Each entry is a
# job whose failure cannot block a merge, so each needs a real reason.
_EXEMPT: Final[dict[str, str]] = {
    "verify-backend.yml::branch-protection-audit": (
        "Informational drift audit; push-to-main and dispatch only, and "
        "environment-gated on `release` so a PR could never run it."
    ),
    "verify-backend.yml::branch-protection-spec": (
        "Advisory: reports that a PR changes the ruleset spec so the live "
        "update is known before merge. Blocking it would fail every PR that "
        "legitimately edits the spec."
    ),
    "verify-cli.yml::cli-release": (
        "Tag-only publish, after the rollup has already reported on the same "
        "commit. Release failures surface via finalize-release."
    ),
    "verify-cli.yml::cli-tag-smoke": (
        "Tag-only. It gates the release path rather than the merge path: "
        "`cli-release` needs it, so a binary that cannot start blocks the "
        "publish. On a PR it is skipped, and wiring a permanently-skipped job "
        "into cli-pass would only teach that rollup to accept a skip."
    ),
}


class SpecShapeError(Exception):
    """Raised when the branch-protection spec cannot be read as expected."""


def _load(path: Path) -> dict[str, object]:
    """Parse a YAML file into a mapping."""
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _jobs(workflow: dict[str, object]) -> dict[str, dict[str, object]]:
    """Return the workflow's top-level jobs."""
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict):
        return {}
    return {str(k): v for k, v in jobs.items() if isinstance(v, dict)}


def _needs_of(job: dict[str, object]) -> set[str]:
    """Normalise a job's ``needs`` into a set of job ids."""
    needs = job.get("needs")
    if isinstance(needs, str):
        return {needs}
    if isinstance(needs, list):
        return {str(item) for item in needs}
    return set()


def _results_references(job: dict[str, object]) -> set[str]:
    """Job ids referenced through ``needs.<id>.result`` in the rollup's steps.

    Only ``.result`` references count. A rollup env block may also carry an
    unrelated ``needs.<id>.outputs.<name>``, and treating that as a gated job
    id would raise a false alarm about a job the rollup does not gate on.
    """
    found: set[str] = set()
    steps = job.get("steps")
    if not isinstance(steps, list):
        return found
    for step in steps:
        if not isinstance(step, dict):
            continue
        env = step.get("env")
        if not isinstance(env, dict):
            continue
        for value in env.values():
            for fragment in str(value).split("needs.")[1:]:
                match = _RESULT_REF.match(fragment)
                if match:
                    found.add(match.group(1))
    return found


def _required_contexts(repo_root: Path) -> set[str]:
    """Every status-check context branch protection requires.

    Raises:
        SpecShapeError: the spec is missing or does not parse into the
            expected shape. Returning an empty set instead would report zero
            problems precisely when the ruleset has drifted, which is when
            property 3 matters most.
    """
    path = repo_root / _BRANCH_PROTECTION
    if not path.exists():
        msg = f"{_BRANCH_PROTECTION} not found (renamed or deleted?)"
        raise SpecShapeError(msg)
    try:
        spec = _load(path)
    except yaml.YAMLError as exc:
        msg = f"{_BRANCH_PROTECTION} failed to parse: {exc}"
        raise SpecShapeError(msg) from exc
    contexts: set[str] = set()
    rulesets = spec.get("rulesets")
    if not isinstance(rulesets, list):
        msg = (
            f"{_BRANCH_PROTECTION} has no top-level 'rulesets' list "
            f"(found {type(rulesets).__name__}). The required contexts cannot "
            "be read, so nothing would be checked."
        )
        raise SpecShapeError(msg)
    for ruleset in rulesets:
        if not isinstance(ruleset, dict):
            continue
        for rule in ruleset.get("rules", []):
            if (
                not isinstance(rule, dict)
                or rule.get("type") != "required_status_checks"
            ):
                continue
            params = rule.get("parameters")
            if not isinstance(params, dict):
                continue
            for check in params.get("required_status_checks", []):
                if isinstance(check, dict) and check.get("context"):
                    contexts.add(str(check["context"]))
    return contexts


def _results_agreement_problems(
    workflow_name: str, rollup_id: str, rollup: dict[str, object]
) -> list[str]:
    """One rollup's ``needs`` and ``RESULTS`` must name the same set."""
    needs = _needs_of(rollup)
    results = _results_references(rollup)
    problems = [
        f"{workflow_name}: '{job_id}' is in {rollup_id}.needs but its result "
        "is never read in RESULTS, so the rollup waits for it without gating "
        "on it."
        for job_id in sorted(needs - results)
    ]
    problems.extend(
        f"{workflow_name}: RESULTS reads '{job_id}' but it is not in "
        f"{rollup_id}.needs, so the expression evaluates to an empty string."
        for job_id in sorted(results - needs)
    )
    return problems


def _rollup_problems(
    workflow_name: str, rollup_ids: tuple[str, ...], repo_root: Path
) -> list[str]:
    """Check one workflow's rollups for coverage and RESULTS agreement."""
    path = repo_root / _WORKFLOW_DIR / workflow_name
    if not path.exists():
        return [f"{workflow_name}: workflow not found (did it get renamed?)"]

    jobs = _jobs(_load(path))
    missing = [rollup_id for rollup_id in rollup_ids if rollup_id not in jobs]
    if missing:
        return [
            f"{workflow_name}: rollup job '{rollup_id}' not found"
            for rollup_id in missing
        ]

    # A workflow may split its jobs across several rollups (one per required
    # context), so coverage is against their union; agreement stays per rollup.
    covered: set[str] = set()
    problems: list[str] = []
    for rollup_id in rollup_ids:
        rollup = jobs[rollup_id]
        covered |= _needs_of(rollup)
        problems.extend(_results_agreement_problems(workflow_name, rollup_id, rollup))

    rollup_list = " / ".join(f"{rollup_id}.needs" for rollup_id in rollup_ids)
    problems[:0] = [
        f"{workflow_name}: job '{job_id}' is not in {rollup_list}, so it "
        "can fail while the required check stays green. Add it, or add an "
        "entry to _EXEMPT with a reason."
        for job_id in sorted(jobs)
        if job_id not in rollup_ids
        and f"{workflow_name}::{job_id}" not in _EXEMPT
        and job_id not in covered
    ]

    return problems


def _produced_contexts(repo_root: Path) -> set[str]:
    """Every check-run context the workflows can emit.

    GitHub falls back to the job id when a job declares no ``name:``, so both
    are contexts a ruleset may legitimately require.
    """
    produced: set[str] = set()
    for pattern in _WORKFLOW_GLOBS:
        for path in sorted((repo_root / _WORKFLOW_DIR).glob(pattern)):
            for job_id, job in _jobs(_load(path)).items():
                name = job.get("name")
                produced.add(name if isinstance(name, str) else job_id)
    return produced


def _triggers(workflow: dict[str, object]) -> dict[str, object]:
    """Return a workflow's trigger mapping.

    YAML 1.1 (which PyYAML implements) reads a bare ``on:`` key as the boolean
    ``True``, so ``workflow["on"]`` is absent for every real workflow file.
    Reading only the string key would leave this gate permanently green while
    reporting nothing, which is worse than not having it.
    """
    for key in (True, "on"):
        found = workflow.get(key)  # type: ignore[arg-type]  # YAML 1.1 coerces the `on:` key to bool
        if isinstance(found, dict):
            return found
    return {}


def _path_filter_problems(repo_root: Path) -> list[str]:
    """No rollup-producing workflow may carry a top-level ``paths:`` filter.

    ``branch_protection.yml`` states this invariant and names this gate as its
    enforcement. It is load-bearing rather than stylistic: a filtered workflow
    does not run on a PR that touches nothing it watches, GitHub never receives
    the required context, and an unreported required context counts as
    unsatisfied, so the PR can never merge.
    """
    problems: list[str] = []
    for workflow_name in _ROLLUPS:
        path = repo_root / _WORKFLOW_DIR / workflow_name
        if not path.exists():
            continue
        triggers = _triggers(_load(path))
        problems.extend(
            f"{workflow_name}: '{event}' carries a top-level 'paths:' filter, "
            "so its required rollup context is not reported on every PR. "
            "GitHub treats a never-reported required context as unsatisfied, "
            "which would block those PRs permanently. Filter per job instead."
            for event, config in triggers.items()
            if isinstance(config, dict) and "paths" in config
        )
    return problems


def _release_pr_status_contexts(repo_root: Path) -> set[str] | None:
    """Contexts ``release-cut.yml`` posts on the release PR, or ``None``.

    ``None`` means the step could not be located, which is itself reported:
    silently reading zero contexts would turn a renamed step into a pass.
    """
    path = repo_root / _WORKFLOW_DIR / _RELEASE_CUT
    if not path.exists():
        return None
    for job in _jobs(_load(path)).values():
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict) or step.get("name") != _STATUS_STEP_NAME:
                continue
            loop = _CONTEXT_LOOP.search(str(step.get("run", "")))
            if loop is None:
                return None
            return set(_QUOTED.findall(loop.group(1)))
    return None


def _release_pr_problems(repo_root: Path) -> list[str]:
    """The release PR's posted statuses must cover every required context."""
    posted = _release_pr_status_contexts(repo_root)
    if posted is None:
        return [
            (
                f"{_RELEASE_CUT}: could not read the '{_STATUS_STEP_NAME}' step's "
                "context list. Every required context is posted there as a commit "
                "status; unread, a missing one would go unnoticed until a release "
                "PR wedged."
            )
        ]
    return [
        f"{_RELEASE_CUT} does not post required context '{context}' on the "
        "release PR. release-please heads run no workflow of their own, so an "
        "unposted required context stays 'Expected, waiting' and the release "
        "PR can never merge."
        for context in sorted(_required_contexts(repo_root) - posted)
    ]


def _context_problems(repo_root: Path) -> list[str]:
    """Every required context must be produced by an unfiltered workflow."""
    produced = _produced_contexts(repo_root)
    problems = [
        f"branch_protection.yml requires context '{context}' but no job "
        "produces that name. GitHub never reports a context nothing emits, "
        "and treats it as unsatisfied, so every PR would be blocked "
        "permanently."
        for context in sorted(_required_contexts(repo_root))
        if context not in produced
    ]
    problems.extend(_path_filter_problems(repo_root))
    return problems


def main(argv: list[str] | None = None) -> int:
    """Run every rollup check, printing each violation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=_REPO_ROOT,
        help="Tree to check. Defaults to this repository; tests point it at a fixture.",
    )
    args = parser.parse_args(argv)

    problems: list[str] = []
    for workflow_name, rollup_ids in _ROLLUPS.items():
        problems.extend(_rollup_problems(workflow_name, rollup_ids, args.repo_root))
    try:
        problems.extend(_context_problems(args.repo_root))
        problems.extend(_release_pr_problems(args.repo_root))
    except SpecShapeError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 2

    if problems:
        print("::error::CI rollup coverage is incomplete:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    covered = ", ".join(f"{wf} -> {'+'.join(jobs)}" for wf, jobs in _ROLLUPS.items())
    print(f"CI rollups complete ({covered}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
