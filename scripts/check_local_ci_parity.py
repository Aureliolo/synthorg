#!/usr/bin/env python3
"""Local <-> CI parity gate (#2322 keystone).

The enforcement stack has three layers: PreToolUse hooks (agent-time),
local git hooks (pre-commit / pre-push), and CI. CI is the authoritative
backstop. If a pre-commit / pre-push hook has NO CI counterpart, then a
``git push --no-verify`` lands a violation that CI never catches. This
gate makes that impossible to introduce silently: every hook wired in
``.pre-commit-config.yaml`` (pre-commit or pre-push stage) MUST be either

* run by the CI ``pre-commit run --all-files`` job (the hybrid job that
  executes the Python / ruff / built-in gates over the whole tree), or
* listed in :data:`_COVERED_ELSEWHERE` with the dedicated CI job that
  covers it (Go in cli.yml, dashboard lint in ci.yml, links in
  lychee.yml, secrets in secret-scan.yml, workflow security in
  zizmor.yml, the dedicated mypy / unit-test jobs, etc.), or
* listed in :data:`_LOCAL_ONLY` as a developer-clone hook that has no
  meaningful CI counterpart (a branch-freshness or migration-immutability
  check that depends on git state CI's fixed-SHA checkout does not have).

**Stage awareness is load-bearing.** ``pre-commit run --all-files`` runs
ONLY the ``pre-commit`` stage by default; nearly every heavy content gate
in this repo is ``stages: [pre-push]``. A single pre-commit-stage
invocation would therefore silently skip the bulk of the suite. This gate
asserts the all-files job covers BOTH parity stages (one invocation per
stage) and that every all-files invocation shares the same SKIP list, so a
hook cannot be skipped at one stage but quietly run at another.

PreToolUse hooks are out of scope: they are agent-time tool-call blocks
with no repo-content counterpart, so CI cannot mirror them.

The gate also enforces the **cardinal rule**: no CI *correctness* job in
``ci.yml`` may be conditioned on which files changed (``dorny/paths-filter``
outputs), because a false "unchanged" -- which the workflow comments admit
can happen on a shallow-checkout race -- silently drops the gate. Pure
build / perf jobs may keep path scoping; they are listed in
:data:`_PATH_SCOPED_BUILD_PERF` with a justification.

Usage::

    uv run python scripts/check_local_ci_parity.py
"""

import argparse
import dataclasses
import re
import sys
from pathlib import Path
from typing import Final

import yaml

_REPO_ROOT_DEFAULT: Final[Path] = Path(__file__).resolve().parent.parent
_PRECOMMIT_CONFIG: Final[str] = ".pre-commit-config.yaml"
_CI_WORKFLOW: Final[str] = ".github/workflows/ci.yml"

# Hook types installed by this repo (``default_install_hook_types``). A
# hook with no explicit ``stages:`` runs at all of these.
_DEFAULT_INSTALLED_STAGES: Final[frozenset[str]] = frozenset(
    {"pre-commit", "commit-msg", "pre-push"}
)
# Only these stages gate repo CONTENT and therefore require a CI mirror.
# commit-msg gates the commit message, not the tree, so it is excluded.
# The all-files job MUST run an invocation at EACH of these stages.
_PARITY_STAGES: Final[frozenset[str]] = frozenset({"pre-commit", "pre-push"})

# Hooks NOT executed by the CI ``pre-commit run --all-files`` job (they
# need a toolchain absent from that python+uv job, or a dedicated job
# already covers them). Each maps to the CI job that DOES cover it.
_COVERED_ELSEWHERE: Final[dict[str, str]] = {
    "go-vet": "cli.yml :: cli-lint (go vet ./...)",
    "go-test": "cli.yml :: cli-test (go test ./...)",
    "golangci-lint": "cli.yml :: cli-lint (golangci-lint run)",
    "eslint-web": "ci.yml :: dashboard-lint (npm run lint)",
    "web-circular": "ci.yml :: dashboard-lint (npm run lint:circular)",
    "web-knip": "ci.yml :: dashboard-lint (npm run lint:knip)",
    "lychee": "lychee.yml :: lychee (internal link check)",
    "hadolint-docker": "ci.yml :: dockerfile-lint (hadolint-action)",
    "gitleaks": "secret-scan.yml :: gitleaks",
    "zizmor": "zizmor.yml :: zizmor (workflow security)",
    "mypy": "ci.yml :: type-check (full-tree mypy)",
    "pytest-unit": "ci.yml :: test-unit (pytest -m unit)",
    "vale": "ci.yml :: lint (dedicated vale prose-lint step)",
    "caddy-validate": "ci.yml :: lint (dedicated caddy validate step)",
    "check-single-migration-per-pr": (
        "ci.yml :: schema-validate (Enforce at most one new revision per PR; "
        "that job fetches the base ref, which the all-files job's shallow "
        "checkout does not)"
    ),
}

# Hooks with NO CI counterpart by design: developer-clone git-state checks
# that depend on history the all-files job's fixed-SHA shallow checkout does
# not have. Running them in CI would fail closed (they require origin/main /
# a base ref) rather than gate content. They MUST be SKIPped by the all-files
# job. Each entry documents why no CI mirror is possible.
_LOCAL_ONLY: Final[dict[str, str]] = {
    "check-push-rebased": (
        "developer-clone branch-freshness check; CI checks out a fixed merge "
        "SHA where 'behind main' is meaningless"
    ),
    "check-no-modify-migration": (
        "developer-clone migration-immutability guard; needs origin/main and "
        "fails closed without it. Mirrored in CI by the PreToolUse "
        "check_no_edit_migration.sh block and the schema-drift-revisions gate"
    ),
}

# ci.yml jobs allowed to keep ``dorny/paths-filter`` conditioning because
# they are pure build / perf steps whose skip cannot hide a correctness
# defect. Every entry needs a justification.
_PATH_SCOPED_BUILD_PERF: Final[dict[str, str]] = {
    "changes": "infra: the paths-filter job itself (produces the outputs)",
    "dashboard-build": "build/perf: production bundle + size budget",
    "dashboard-storybook-build": "build: Storybook static build",
    "ci-pass": "aggregate rollup (always-run, gates on other jobs' results)",
    "branch-protection-audit": "post-merge infra audit, main-only (not a PR gate)",
}

# cli.yml jobs allowed to keep ``cli-changes`` conditioning -- the Go lint /
# test / vuln CORRECTNESS jobs were de-conditioned (#2322), leaving only these
# build / perf jobs scoped. Each skip cannot hide a correctness defect.
_CLI_PATH_SCOPED_BUILD_PERF: Final[dict[str, str]] = {
    "cli-build": "build: 6-platform cross-compile + smoke",
    "cli-bench": "perf: HEAD-vs-merge-base benchmark A/B (PR-only)",
    "cli-fuzz": "perf/slow: 45-min fuzzing, main-push only (not a PR gate)",
}

# Output names of a paths-filter ``changes`` job that are NOT changed-file
# conditions (event-type guards), so referencing them in an ``if:`` does not
# violate the cardinal rule.
_NON_PATH_OUTPUTS: Final[frozenset[str]] = frozenset({"is_release_please"})


@dataclasses.dataclass(frozen=True)
class _CardinalWorkflow:
    """A workflow whose correctness jobs the cardinal rule polices.

    ``changes_re`` extracts changed-file output references from a job ``if:``;
    ``non_path_outputs`` are event-type guards that do not count; ``build_perf``
    is the justified path-scoped allowlist (build / perf jobs).
    """

    path: str
    changes_re: re.Pattern[str]
    non_path_outputs: frozenset[str]
    build_perf: dict[str, str]


# Workflows that carry CORRECTNESS jobs. codspeed/docker/lighthouse run only
# perf / build / publish jobs (no correctness), so the cardinal rule does not
# apply to them; ci.yml and cli.yml are the two with correctness gates.
_CARDINAL_WORKFLOWS: Final[tuple[_CardinalWorkflow, ...]] = (
    _CardinalWorkflow(
        path=_CI_WORKFLOW,
        changes_re=re.compile(r"needs\.changes\.outputs\.([A-Za-z_][A-Za-z0-9_]*)"),
        non_path_outputs=_NON_PATH_OUTPUTS,
        build_perf=_PATH_SCOPED_BUILD_PERF,
    ),
    _CardinalWorkflow(
        path=".github/workflows/cli.yml",
        changes_re=re.compile(r"needs\.cli-changes\.outputs\.([A-Za-z_][A-Za-z0-9_]*)"),
        # cli-changes exposes only ``changed`` -- a pure changed-file output, no
        # event guards -- so nothing is exempt here.
        non_path_outputs=frozenset(),
        build_perf=_CLI_PATH_SCOPED_BUILD_PERF,
    ),
)

# ``--hook-stage pre-push`` or ``--hook-stage=pre-push`` in a CI run string.
_HOOK_STAGE_RE: Final[re.Pattern[str]] = re.compile(r"--hook-stage[=\s]+(\S+)")
_ALL_FILES_MARKER: Final[str] = "pre-commit run --all-files"


def _effective_stages(hook: dict[str, object]) -> frozenset[str]:
    """Return the stages a hook runs at, resolving the empty-``stages`` default."""
    raw = hook.get("stages")
    if raw is None:
        return _DEFAULT_INSTALLED_STAGES
    if isinstance(raw, list):
        return frozenset(str(item) for item in raw)
    return frozenset({str(raw)})


def _load_yaml(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        msg = f"{path}: expected a mapping at the document root"
        raise ValueError(msg)
    return data


def _local_hook_ids(config: dict[str, object]) -> list[str]:
    """Return every hook id wired at a parity-relevant stage."""
    ids: list[str] = []
    repos = config.get("repos")
    if not isinstance(repos, list):
        return ids
    for repo in repos:
        if not isinstance(repo, dict):
            continue
        hooks = repo.get("hooks")
        if not isinstance(hooks, list):
            continue
        for hook in hooks:
            if not isinstance(hook, dict):
                continue
            hook_id = hook.get("id")
            if not isinstance(hook_id, str):
                continue
            if _effective_stages(hook) & _PARITY_STAGES:
                ids.append(hook_id)
    return ids


def _parse_skip(raw: object) -> frozenset[str]:
    """Split a ``SKIP`` env string into a set of hook ids."""
    return frozenset(part.strip() for part in str(raw or "").split(",") if part.strip())


def _all_files_invocations(ci: dict[str, object]) -> list[tuple[str, frozenset[str]]]:
    """Return ``(hook_stage, skip_ids)`` for each CI all-files pre-commit step.

    ``hook_stage`` is parsed from ``--hook-stage <stage>`` in the run string,
    defaulting to ``pre-commit`` (pre-commit's own default). ``skip_ids`` is the
    step's ``SKIP`` env, falling back to the job-level ``SKIP`` env so a single
    shared SKIP at the job level applies to every all-files step.
    """
    out: list[tuple[str, frozenset[str]]] = []
    jobs = ci.get("jobs")
    if not isinstance(jobs, dict):
        return out
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        job_env = job.get("env")
        job_skip = job_env.get("SKIP") if isinstance(job_env, dict) else None
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict):
                continue
            run = step.get("run")
            if not isinstance(run, str) or _ALL_FILES_MARKER not in run:
                continue
            match = _HOOK_STAGE_RE.search(run)
            stage = match.group(1) if match else "pre-commit"
            step_env = step.get("env")
            step_skip = step_env.get("SKIP") if isinstance(step_env, dict) else None
            raw = step_skip if step_skip is not None else job_skip
            out.append((stage, _parse_skip(raw)))
    return out


def _parity_violations(repo_root: Path) -> list[str]:
    """Return parity problems between local hooks and CI coverage."""
    config = _load_yaml(repo_root / _PRECOMMIT_CONFIG)
    ci = _load_yaml(repo_root / _CI_WORKFLOW)
    local_ids = _local_hook_ids(config)
    invocations = _all_files_invocations(ci)

    problems: list[str] = []
    if not invocations:
        problems.append(
            f"{_CI_WORKFLOW}: no '{_ALL_FILES_MARKER}' step found -- the hybrid "
            "parity job is missing, so the Python gate set has no CI mirror."
        )
        return problems

    # The all-files job must cover EVERY parity stage. ``pre-commit run
    # --all-files`` runs only the pre-commit stage; the pre-push-only gates
    # (the bulk of the suite) need a second ``--hook-stage pre-push`` step.
    covered_stages = {stage for stage, _ in invocations}
    missing_stages = _PARITY_STAGES - covered_stages
    if missing_stages:
        problems.append(
            f"{_CI_WORKFLOW}: the all-files job runs stages "
            f"{sorted(covered_stages)} but not {sorted(missing_stages)}. "
            "Pre-push-only gates would have NO CI backstop. Add a "
            f"'{_ALL_FILES_MARKER} --hook-stage <stage>' step for each "
            "missing stage."
        )

    # Every all-files invocation must share one SKIP list, or a hook could be
    # skipped at one stage but run at another (the bug this gate prevents).
    skip_sets = {skip for _, skip in invocations}
    if len(skip_sets) > 1:
        rendered = [sorted(s) for s in skip_sets]
        problems.append(
            f"{_CI_WORKFLOW}: the all-files invocations disagree on SKIP "
            f"({rendered}). Use one shared SKIP (job-level env) so coverage is "
            "identical at every stage."
        )
    skip: frozenset[str] = frozenset().union(*skip_sets) if skip_sets else frozenset()
    justified = set(_COVERED_ELSEWHERE) | set(_LOCAL_ONLY)

    for hook_id in local_ids:
        covered_by_all_files = hook_id not in skip
        if not (covered_by_all_files or hook_id in justified):
            problems.append(
                f"hook '{hook_id}' has NO CI counterpart: it is SKIPped by the "
                "all-files job but absent from _COVERED_ELSEWHERE / _LOCAL_ONLY. "
                "A --no-verify push could land a violation CI never catches. "
                "Either stop skipping it, or document its coverage."
            )

    # Every SKIPped hook must be justified, and the SKIP list must match the
    # documented coverage maps exactly (no drift in either direction).
    problems.extend(
        f"all-files SKIP lists '{skipped}' but it is not in "
        "_COVERED_ELSEWHERE or _LOCAL_ONLY: an unjustified skip is a "
        "silent coverage hole."
        for skipped in sorted(skip)
        if skipped not in justified
    )
    for covered in sorted(_COVERED_ELSEWHERE):
        if covered not in skip:
            problems.append(
                f"_COVERED_ELSEWHERE documents '{covered}' as covered by a "
                "dedicated job, but the all-files job does not SKIP it (so it "
                "runs in BOTH places, or the entry is stale). Align the SKIP list."
            )
        if covered not in local_ids:
            problems.append(
                f"_COVERED_ELSEWHERE references '{covered}', which is not a "
                "parity-stage hook in .pre-commit-config.yaml (stale entry)."
            )
    for local_only in sorted(_LOCAL_ONLY):
        if local_only not in skip:
            problems.append(
                f"_LOCAL_ONLY documents '{local_only}' as having no CI "
                "counterpart, but the all-files job does not SKIP it -- it would "
                "run in CI and fail closed on the shallow checkout. Add it to SKIP."
            )
        if local_only not in local_ids:
            problems.append(
                f"_LOCAL_ONLY references '{local_only}', which is not a "
                "parity-stage hook in .pre-commit-config.yaml (stale entry)."
            )
    return problems


def _cardinal_rule_violations(repo_root: Path) -> list[str]:
    """Flag correctness jobs (ci.yml + cli.yml) conditioned on changed files."""
    problems: list[str] = []
    for wf in _CARDINAL_WORKFLOWS:
        workflow = _load_yaml(repo_root / wf.path)
        jobs = workflow.get("jobs")
        if not isinstance(jobs, dict):
            continue
        for job_name, job in jobs.items():
            if not isinstance(job, dict) or not isinstance(job_name, str):
                continue
            condition = job.get("if")
            if not isinstance(condition, str):
                continue
            referenced = set(wf.changes_re.findall(condition))
            changed_file_outputs = referenced - wf.non_path_outputs
            if changed_file_outputs and job_name not in wf.build_perf:
                outs = ", ".join(sorted(changed_file_outputs))
                problems.append(
                    f"{wf.path} :: job '{job_name}' is a correctness job "
                    f"conditioned on changed-file output(s) [{outs}]. A "
                    "shallow-checkout race can falsely skip it. Remove the "
                    "paths-filter condition, or justify it in the workflow's "
                    "build/perf allowlist."
                )
    return problems


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. ``0`` clean, ``1`` on a parity / cardinal-rule violation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=_REPO_ROOT_DEFAULT)
    args = parser.parse_args(argv)
    repo_root: Path = args.repo_root.resolve()

    problems = _parity_violations(repo_root)
    problems.extend(_cardinal_rule_violations(repo_root))

    if not problems:
        return 0
    print("Local <-> CI parity / cardinal-rule violations:", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
