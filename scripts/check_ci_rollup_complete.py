#!/usr/bin/env python3
"""Gate: a sentinel rollup must cover every job in its workflow.

Branch protection requires rollup contexts (``CI Pass``, ``CLI Pass``,
``Docker Pass``, ...) rather than each job, so a rollup gates only what its
``needs:`` names: add a job and forget to wire it in, and it runs, goes
red, and the required check stays green.

Three properties:

1. Every top-level job appears in some rollup's ``needs``, or sits in
   :data:`_EXEMPT` with a justification.
2. Each rollup's hand-written ``RESULTS`` env block names exactly what that
   rollup needs. In ``needs`` but not ``RESULTS`` waits without gating; the
   reverse expands to an empty string.
3. Every context ``branch_protection.yml`` requires is produced by some job.
   GitHub treats a never-reported required context as unsatisfied, blocking
   every PR permanently, so this is what makes renaming workflows safe.

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
import sys
from pathlib import Path
from typing import Final

import yaml

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_WORKFLOW_DIR: Final[str] = ".github/workflows"
_BRANCH_PROTECTION: Final[str] = ".github/branch_protection.yml"

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
                if ".result" not in fragment:
                    continue
                job_id = fragment.split(".result")[0].strip()
                if job_id and job_id == job_id.strip("{}$ "):
                    found.add(job_id)
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
    for path in sorted((repo_root / _WORKFLOW_DIR).glob("*.yml")):
        for job_id, job in _jobs(_load(path)).items():
            name = job.get("name")
            produced.add(name if isinstance(name, str) else job_id)
    return produced


def _context_problems(repo_root: Path) -> list[str]:
    """Every required context must be produced by some job."""
    produced = _produced_contexts(repo_root)
    return [
        f"branch_protection.yml requires context '{context}' but no job "
        "produces that name. GitHub never reports a context nothing emits, "
        "and treats it as unsatisfied, so every PR would be blocked "
        "permanently."
        for context in sorted(_required_contexts(repo_root))
        if context not in produced
    ]


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
