#!/usr/bin/env python3
"""Gate: CI workflow resilience invariants.

Enforces two invariants across ``.github/workflows/*.yml`` so the
resilience hardening in this PR cannot silently regress:

1. **Every job declares ``timeout-minutes``.** A job without it inherits
   GitHub's 6-hour default, so a single black-holed network call (a hung
   registry pull, a stuck OIDC token mint) holds a runner for hours
   instead of being reaped in minutes. Reusable-workflow-call jobs (a job
   whose body is a top-level ``uses:``) are exempt -- they cannot set
   ``timeout-minutes``; the called workflow owns its own job timeouts.

2. **Every step using an external upload/OIDC action without internal
   retry is wrapped in a fail-closed retry ladder.** The incident that
   motivated this gate: ``codecov/codecov-action`` failed on a 503 from
   the OIDC ``getIDToken()`` mint -- an UNHANDLED error BEFORE the upload,
   which the action's ``fail_ci_if_error: false`` never sees -- and
   crashed the job. The fix is the house step-duplication ladder
   (see ``.github/actions/checkout``): intermediate attempts carry
   ``continue-on-error: true`` so the run advances to the retry, and the
   final attempt carries neither guard so a genuinely persistent outage
   fails CI loud. This gate checks, per (job, enforced-action), that the
   action's steps include at least one with ``continue-on-error`` (a
   ladder exists) AND at least one without (a fail-closed final attempt).
   A single bare step fails the first half; an all-soft-failed ladder
   fails the second.

3. **Every ``retry_cmd.sh`` call site bounds its ladder in wall-clock.**
   A retry ladder is only useful if it can finish inside the job that runs
   it. ``uv``'s installer was wrapped in 5 attempts x a 120s per-attempt
   timeout plus ~3m45s of backoff -- an 825s worst case inside jobs
   budgeted 300-600s -- so the ladder could never exhaust: the runner was
   reaped mid-retry and a stalled CDN surfaced as an opaque ``cancelled``
   job with no diagnosis, its final attempts unreachable dead config. The
   apt call sites had no per-attempt bound at all and hung outright. Both
   are the same defect: an unbounded ladder. ``RETRY_CMD_DEADLINE`` is the
   bound, so this gate requires every call site to set it, via step
   ``env:`` or an inline ``VAR=n`` prefix.

4. **A wrapped action is reached only through its wrapper.** Where the
   ladder lives in a dedicated composite (``.github/actions/checkout``,
   ``.github/actions/download-artifact``), calling the upstream action
   directly silently opts out of the retry. ``actions/download-artifact``
   earned its wrapper when a ``(403) Forbidden: Error from intermediary``
   -- which the action itself classifies as non-retryable -- killed two
   jobs and the required ``CI Pass`` with them, on a head whose identical
   jobs had passed three times before with the same token. Only the paths
   in ``_WRAPPED_ACTIONS`` may name their upstream action.

The enforced set is deliberately narrow: the external upload/OIDC actions
that lack their own retry AND sit on an important / required path. Other
externally-dependent actions are excluded by design (see ``_EXCLUDED``)
because they retry internally or are non-blocking feature-advisory.

Invariants 1-2 scan ``.github/workflows/``; invariants 3-4 also scan
``.github/actions/*/action.yml``, because composite actions both host
every ``retry_cmd.sh`` call site and can bypass a wrapper themselves.

This is a no-baseline gate: the convention passes clean from day one. If
it flags an existing workflow, fix the workflow -- do NOT add a baseline.

Usage::

    python scripts/check_ci_workflow_resilience.py <file>...   # pre-commit
    python scripts/check_ci_workflow_resilience.py --scan-all  # CI / manual
"""

import argparse
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Final

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS_ROOT = _REPO_ROOT / ".github" / "workflows"
_ACTIONS_ROOT = _REPO_ROOT / ".github" / "actions"

_RETRY_HELPER: Final[str] = "retry_cmd.sh"
_DEADLINE_VAR: Final[str] = "RETRY_CMD_DEADLINE"

# Upstream actions that exactly one in-repo wrapper is allowed to call, so
# the wrapper's retry ladder cannot be bypassed. Maps the upstream action to
# the only path permitted to reference it.
_WRAPPED_ACTIONS: Final[dict[str, str]] = {
    "actions/download-artifact": ".github/actions/download-artifact/action.yml",
}

# External upload / OIDC actions that lack internal retry and sit on an
# important or required path. A step using one MUST be in a fail-closed
# retry ladder (see module docstring). Matched on the version-stripped
# ``owner/name`` (or ``owner/name/subpath``) prefix of the ``uses:`` ref.
_ENFORCED_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        # The incident: OIDC getIDToken() 503 crashed the coverage upload.
        "codecov/codecov-action",
        # Required check (CodSpeed Pass); same OIDC class, would block PR
        # merges if a CodSpeed 503 went unguarded.
        "CodSpeedHQ/action",
    }
)

# Externally-dependent upload/OIDC actions deliberately NOT enforced, with
# the reason each is safe to leave un-laddered. Documented here so a future
# maintainer sees the exclusion was a decision, not an oversight.
_EXCLUDED: Final[dict[str, str]] = {
    "actions/deploy-pages": (
        "non-blocking (push-to-main docs deploy, self-heals next push); the"
        " action polls the Pages API with internal retry; laddering is"
        " complicated by its page_url output dependency"
    ),
    "actions/ai-inference": (
        "cosmetic feature-advisory (AI-generated release-notes Highlights);"
        " already continue-on-error, a failure drops a nicety, not infra"
    ),
    "github/codeql-action/upload-sarif": (
        "best-effort security telemetry guarded by if: always(); the CodeQL"
        " action retries the upload internally, and laddering 6+ call sites"
        " is disproportionate to the non-blocking impact"
    ),
}

_TIMEOUT_KEY: Final[str] = "timeout-minutes"


def _iter_workflow_files() -> Iterable[Path]:
    """Walk ``.github/workflows/`` and ``.github/actions/`` for YAML files."""
    for root in (_WORKFLOWS_ROOT, _ACTIONS_ROOT):
        if not root.exists():
            continue
        for pattern in ("*.yml", "*.yaml"):
            yield from sorted(root.rglob(pattern))


def _action_id(uses: str) -> str:
    """Strip the ``@<ref>`` version from a ``uses:`` value.

    ``codecov/codecov-action@fb8b...`` -> ``codecov/codecov-action``.
    A leading host-qualified fork form (``owner/repo/.github/...@sha``)
    is returned as-is minus the version; callers match on prefix.
    """
    return uses.split("@", 1)[0].strip()


def _step_has_continue_on_error(step: dict[str, object]) -> bool:
    """Return True if the step carries a truthy ``continue-on-error``.

    A literal ``true`` or any ``${{ ... }}`` expression counts as a
    ladder-intermediate guard; the only non-guarding value is an explicit
    ``false`` / absent key.
    """
    value = step.get("continue-on-error", False)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"", "false"}
    return bool(value)


def _enforced_action_for(uses: str) -> str | None:
    """Return the enforced-action key matching ``uses``, or None."""
    action = _action_id(uses)
    for enforced in _ENFORCED_ACTIONS:
        if action == enforced or action.startswith(f"{enforced}/"):
            return enforced
    return None


def _job_steps(job: dict[str, object]) -> list[dict[str, object]]:
    """Return the job's step mappings (empty for reusable-call jobs)."""
    steps = job.get("steps")
    if not isinstance(steps, list):
        return []
    return [step for step in steps if isinstance(step, dict)]


def _check_job_timeout(job_name: str, job: dict[str, object]) -> list[str]:
    """Return a violation message if the job lacks ``timeout-minutes``."""
    # A reusable-workflow-call job (a top-level ``uses:`` STRING) cannot
    # declare timeout-minutes; the called workflow owns its job timeouts.
    # Require a string so a malformed bare ``uses:`` (parses to None) is
    # still timeout-checked rather than silently exempted.
    if isinstance(job.get("uses"), str):
        return []
    if _TIMEOUT_KEY not in job:
        return [
            (
                f"job '{job_name}': no {_TIMEOUT_KEY} (inherits the 6-hour default;"
                " a hung network call holds a runner for hours)"
            )
        ]
    return []


def _check_job_ladders(job_name: str, job: dict[str, object]) -> list[str]:
    """Return violations for enforced actions not in a fail-closed ladder.

    A correct ladder is a run of one-or-more ``continue-on-error`` attempts
    followed by exactly one unguarded final attempt. Steps are walked in
    order, per enforced action, via a small state machine so that:

    * TWO independent ladders for the same action in one job (e.g. ci.yml's
      coverage + test-results Codecov uploads) are EACH validated -- a naive
      aggregate "any guarded AND any unguarded" check would let a malformed
      ``[bare, bare]`` ladder hide behind a sibling ``[guarded, guarded,
      bare]`` ladder; and
    * the backoff ``run:`` steps that interleave a ladder's attempts do not
      split it (only the enforced-action steps drive the state).

    Per enforced action, ``open`` tracks whether a guarded attempt has been
    seen since the last unguarded final attempt (a ladder is "open"). An
    unguarded step with no open ladder is a bare single upload (violation);
    guarded attempts left open at the end are a terminal soft-fail with no
    fail-closed final attempt (violation).
    """
    open_ladder: dict[str, bool] = {}
    violations: list[str] = []
    for step in _job_steps(job):
        uses = step.get("uses")
        if not isinstance(uses, str):
            continue
        action = _enforced_action_for(uses)
        if action is None:
            continue
        if _step_has_continue_on_error(step):
            open_ladder[action] = True
            continue
        if not open_ladder.get(action, False):
            violations.append(
                f"job '{job_name}': '{action}' has an unguarded step with no"
                " preceding continue-on-error retry attempt: a single"
                " external/OIDC upload that fails CI on one transient blip."
                " Wrap it in a fail-closed retry ladder."
            )
        open_ladder[action] = False
    violations.extend(
        f"job '{job_name}': '{action}' ladder has only continue-on-error"
        " attempts and no fail-closed final attempt: a persistent outage"
        " would be silently swallowed."
        for action, still_open in sorted(open_ladder.items())
        if still_open
    )
    return violations


def _check_wrapped_action(
    context: str, rel_path: str, step: dict[str, object]
) -> list[str]:
    """Return violations for a step bypassing a wrapper's retry ladder.

    Args:
        context: Human-readable location (job or composite-action step).
        rel_path: Repo-relative path of the file being scanned.
        step: The step mapping.

    Returns:
        One message when the step calls a wrapped action from outside its
        wrapper, empty otherwise.
    """
    uses = step.get("uses")
    if not isinstance(uses, str):
        return []
    action = _action_id(uses)
    wrapper = _WRAPPED_ACTIONS.get(action)
    if wrapper is None or rel_path == wrapper:
        return []
    return [
        (
            f"{context}: uses `{action}` directly; call `./{Path(wrapper).parent.as_posix()}`"
            " instead so the retry ladder cannot be bypassed"
        )
    ]


def _step_env_has_deadline(step: dict[str, object]) -> bool:
    """Return True if the step's ``env:`` sets the deadline."""
    env = step.get("env")
    return isinstance(env, dict) and _DEADLINE_VAR in env


def _retry_lines_missing_deadline(run: str) -> list[str]:
    """Return the ``retry_cmd.sh`` invocations lacking an inline deadline.

    A call site may set the deadline as an inline ``VAR=n cmd`` prefix
    instead of via step ``env:``, so each invocation line is inspected
    separately: one prefixed line does not cover an unprefixed sibling.

    Args:
        run: The step's shell body.

    Returns:
        The offending lines, stripped, in source order.
    """
    return [
        line.strip()
        for line in run.splitlines()
        if _RETRY_HELPER in line and _DEADLINE_VAR not in line
    ]


def _check_retry_deadlines(context: str, step: dict[str, object]) -> list[str]:
    """Return violations for a step invoking the retry helper unbounded.

    Args:
        context: Human-readable location (job or composite-action step).
        step: The step mapping.

    Returns:
        One message per unbounded invocation.
    """
    run = step.get("run")
    if not isinstance(run, str) or _RETRY_HELPER not in run:
        return []
    if _step_env_has_deadline(step):
        return []
    return [
        (
            f"{context}: `{line}` runs {_RETRY_HELPER} without {_DEADLINE_VAR}"
            " (an unbounded ladder outlives its job budget, so the reaper"
            " turns a retryable stall into an opaque cancelled job)"
        )
        for line in _retry_lines_missing_deadline(run)
    ]


def _scan_composite_action(data: dict[str, object], rel_path: str) -> list[str]:
    """Return retry-deadline violations for a composite action file.

    Composite actions have no ``jobs``; their steps hang off ``runs.steps``.
    Invariants 3 and 4 apply -- an action cannot declare ``timeout-minutes``,
    and the enforced upload actions are not used from one.

    Args:
        data: The parsed ``action.yml``.
        rel_path: Repo-relative path, so a wrapper can exempt itself.

    Returns:
        Violation messages, empty when the action is compliant.
    """
    runs = data.get("runs")
    if not isinstance(runs, dict):
        return []
    steps = runs.get("steps")
    if not isinstance(steps, list):
        return []
    violations: list[str] = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        name = step.get("name")
        label = str(name) if isinstance(name, str) else f"step {index + 1}"
        violations.extend(_check_retry_deadlines(f"'{label}'", step))
        violations.extend(_check_wrapped_action(f"'{label}'", rel_path, step))
    return violations


def _relative_path(path: Path) -> str:
    """Return *path* relative to the repo root, or absolute if outside it."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def _scan_file(path: Path) -> list[str]:
    """Return all violation messages for one workflow file."""
    rel_path = _relative_path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        # str(exc) carries PyYAML's line/column (and is safe here -- this is
        # a CLI gate printing to stderr, not a logger that could serialise a
        # secret); without it a maintainer cannot locate the syntax error.
        return [f"YAML parse error: {type(exc).__name__}: {exc}"]
    if not isinstance(data, dict):
        return []
    jobs = data.get("jobs")
    if not isinstance(jobs, dict):
        return _scan_composite_action(data, rel_path)
    violations: list[str] = []
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        name = str(job_name)
        violations.extend(_check_job_timeout(name, job))
        violations.extend(_check_job_ladders(name, job))
        for step in _job_steps(job):
            violations.extend(_check_retry_deadlines(f"job '{name}'", step))
            violations.extend(_check_wrapped_action(f"job '{name}'", rel_path, step))
    return violations


def _scan_paths(paths: Iterable[Path]) -> int:
    """Scan each path; print violations; return the shell exit code."""
    failed = False
    for path in paths:
        if not path.exists() or path.suffix not in (".yml", ".yaml"):
            continue
        violations = _scan_file(path)
        if not violations:
            continue
        failed = True
        rel = _relative_path(path)
        for message in violations:
            print(f"{rel}: {message}", file=sys.stderr)
    if failed:
        print(
            "\nCI workflow resilience gate failed. Add timeout-minutes to every"
            " job, wrap every enforced external/OIDC upload action in a"
            " fail-closed retry ladder (see .github/actions/checkout for the"
            " pattern), give every retry_cmd.sh call site a"
            " RETRY_CMD_DEADLINE sized below its job budget, and reach every"
            " wrapped action through its wrapper rather than upstream. To"
            " exclude an action deliberately, add it to _EXCLUDED in"
            " scripts/check_ci_workflow_resilience.py with a reason.",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Enforce CI workflow resilience invariants.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Files to check (pre-commit supplies these).",
    )
    parser.add_argument(
        "--scan-all",
        action="store_true",
        help="Scan every workflow file (CI / manual mode).",
    )
    args = parser.parse_args(argv)

    # Self-consistency: no excluded action may also be MATCHED by an enforced
    # prefix, else a step using it would be enforced despite being listed as
    # excluded. This mirrors _enforced_action_for's prefix semantics (exact
    # set intersection would miss e.g. enforced 'github/codeql-action' vs
    # excluded 'github/codeql-action/upload-sarif'). Fail as a setup error
    # (exit 2), not a silent exclusion win.
    overlap = sorted(
        excluded
        for excluded in _EXCLUDED
        for enforced in _ENFORCED_ACTIONS
        if excluded == enforced or excluded.startswith(f"{enforced}/")
    )
    if overlap:
        print(
            f"setup error: excluded actions also matched as enforced: {overlap}",
            file=sys.stderr,
        )
        return 2

    if args.scan_all or not args.paths:
        targets = list(_iter_workflow_files())
    else:
        targets = [Path(p).resolve() for p in args.paths]
    return _scan_paths(targets)


if __name__ == "__main__":
    sys.exit(main())
