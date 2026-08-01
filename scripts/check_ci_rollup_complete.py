#!/usr/bin/env python3
"""Gate: a sentinel rollup must cover every job in its workflow.

Branch protection requires rollup contexts (``CI Pass``, ``CLI Pass``,
``Docker Pass``) rather than each job, so a rollup gates only what its
``needs:`` names: add a job and forget to wire it in, and it runs, goes
red, and the required check stays green.

Three properties:

1. Every top-level job appears in the rollup's ``needs``, or sits in
   :data:`_EXEMPT` with a justification.
2. The hand-written ``RESULTS`` env block names exactly the same set. In
   ``needs`` but not ``RESULTS`` waits without gating; the reverse
   expands to an empty string.
3. Every context ``branch_protection.yml`` requires is some job's
   ``name:``. GitHub treats a never-reported required context as
   unsatisfied, blocking every PR permanently, so this is what makes
   renaming workflows safe.

No-baseline gate: passes clean from day one.

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

# workflow filename -> the job id of its sentinel rollup.
_ROLLUPS: Final[dict[str, str]] = {
    "ci.yml": "ci-pass",
    "cli.yml": "cli-pass",
    "docker.yml": "docker-pass",
}

# `<workflow>::<job>` -> why the rollup does not depend on it. Each entry is a
# job whose failure cannot block a merge, so each needs a real reason.
_EXEMPT: Final[dict[str, str]] = {
    "ci.yml::branch-protection-audit": (
        "Informational drift audit; push-to-main and dispatch only, and "
        "environment-gated on `release` so a PR could never run it."
    ),
    "cli.yml::cli-release": (
        "Tag-only publish, after the rollup has already reported on the same "
        "commit. Release failures surface via finalize-release."
    ),
}


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
    """Job ids referenced through ``needs.<id>.result`` in the rollup's steps."""
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
                job_id = fragment.split(".result")[0].strip()
                if job_id and job_id == job_id.strip("{}$ "):
                    found.add(job_id)
    return found


def _required_contexts(repo_root: Path) -> set[str]:
    """Every status-check context branch protection requires."""
    spec = _load(repo_root / _BRANCH_PROTECTION)
    contexts: set[str] = set()
    rulesets = spec.get("rulesets")
    if not isinstance(rulesets, list):
        return contexts
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


def _rollup_problems(workflow_name: str, rollup_id: str, repo_root: Path) -> list[str]:
    """Check one workflow's rollup for coverage and RESULTS agreement."""
    path = repo_root / _WORKFLOW_DIR / workflow_name
    if not path.exists():
        return [f"{workflow_name}: workflow not found (did it get renamed?)"]

    jobs = _jobs(_load(path))
    if rollup_id not in jobs:
        return [f"{workflow_name}: rollup job '{rollup_id}' not found"]

    rollup = jobs[rollup_id]
    needs = _needs_of(rollup)
    results = _results_references(rollup)
    problems: list[str] = []

    problems.extend(
        f"{workflow_name}: job '{job_id}' is not in {rollup_id}.needs, so it "
        "can fail while the required check stays green. Add it, or add an "
        "entry to _EXEMPT with a reason."
        for job_id in sorted(jobs)
        if job_id != rollup_id
        and f"{workflow_name}::{job_id}" not in _EXEMPT
        and job_id not in needs
    )

    problems.extend(
        f"{workflow_name}: '{job_id}' is in {rollup_id}.needs but its result "
        "is never read in RESULTS, so the rollup waits for it without gating "
        "on it."
        for job_id in sorted(needs - results)
    )
    problems.extend(
        f"{workflow_name}: RESULTS reads '{job_id}' but it is not in "
        f"{rollup_id}.needs, so the expression evaluates to an empty string."
        for job_id in sorted(results - needs)
    )

    return problems


def _context_problems(repo_root: Path) -> list[str]:
    """Every required context must be some job's name."""
    produced: set[str] = set()
    for path in sorted((repo_root / _WORKFLOW_DIR).glob("*.yml")):
        for job in _jobs(_load(path)).values():
            name = job.get("name")
            if isinstance(name, str):
                produced.add(name)

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
    for workflow_name, rollup_id in _ROLLUPS.items():
        problems.extend(_rollup_problems(workflow_name, rollup_id, args.repo_root))
    problems.extend(_context_problems(args.repo_root))

    if problems:
        print("::error::CI rollup coverage is incomplete:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    covered = ", ".join(f"{wf} -> {job}" for wf, job in _ROLLUPS.items())
    print(f"CI rollups complete ({covered}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
