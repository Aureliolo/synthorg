#!/usr/bin/env python3
"""Gate: CI workflow resilience invariants.

Enforces eight invariants across the CI definitions so the resilience
hardening they carry cannot silently regress:

1. **Every job declares ``timeout-minutes``.** A job without it inherits
   GitHub's 6-hour default, so a single black-holed network call (a hung
   registry pull, a stuck OIDC token mint) holds a runner for hours
   instead of being reaped in minutes. Reusable-workflow-call jobs (a job
   whose body is a top-level ``uses:``) are exempt -- they cannot set
   ``timeout-minutes``; the called workflow owns its own job timeouts.

2. **Every step using an external upload/OIDC action without internal
   retry is wrapped in a fail-closed retry ladder.** Such an action can
   5xx while minting its OIDC token (``getIDToken()``), which happens
   BEFORE the upload it guards, so ``fail_ci_if_error: false`` never sees
   the error and the job crashes outright. The house step-duplication
   ladder covers that window (see ``.github/actions/checkout``):
   intermediate attempts carry ``continue-on-error: true`` so the run
   advances to the retry, and the final attempt carries neither guard so
   a genuinely persistent outage fails CI loud. This gate checks, per
   (job, enforced-action), that the action's steps include at least one
   with ``continue-on-error`` (a ladder exists) AND at least one without
   (a fail-closed final attempt). A single bare step fails the first
   half; an all-soft-failed ladder fails the second.

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

4. **A local action resolves where it is used, and a wrapped action is
   reached only through its wrapper.** Two halves of one rule.

   A ``uses: ./...`` step loads from the runner's workspace, so it needs an
   EARLIER checkout in the same job that actually put the action on disk.
   Miss that and the job dies with "Can't find 'action.yml' ... Did you
   forget to run actions/checkout" -- including the subtle case where the
   job does check out, but sparsely, without the action's path. Which steps
   count as a checkout is decided on the resolved action id
   (``_is_checkout_action``): upstream ``actions/checkout`` plus the in-repo
   retry wrapper in either reference form, and nothing that merely resembles
   one, because a false positive there would fold absent coverage in as full
   and silently suppress every real violation in the rest of the job.

   ``actions/download-artifact`` earned its wrapper when a ``(403)
   Forbidden: Error from intermediary`` -- which the action itself
   classifies as non-retryable -- killed two jobs and the required ``CI
   Pass`` with them, on a head whose identical jobs had passed three times
   before with the same token. Reaching upstream is legitimate exactly
   where the wrapper is unresolvable by the rule above, so the two halves
   decide each other structurally rather than through an allowlist that
   would go stale the moment a job gained or lost a checkout.

5. **Every artifact-consuming job grants ``actions: read`` and passes a
   token to the download.** ``actions/download-artifact``'s default path
   uses the runtime token, which is scoped to the CURRENT run ATTEMPT.
   Artifacts uploaded by attempt 1 are therefore invisible to attempt 2,
   and a re-run of a failed consumer dies with ``GetSignedArtifactURL
   ... (404) workflow run not found``. Both halves are needed to escape
   that: an explicit ``github-token`` switches the action onto the REST
   API path, and ``actions: read`` is the scope that path requires. One
   without the other still 404s, so the gate demands both -- otherwise
   the documented "re-run the failed job" recovery is unreachable
   config.

   The obligation is transitive. A job that never mentions the download
   directly still consumes artifacts when it calls a composite action
   that does, so the rule is checked against the closure of local
   composites that reach a download (``_artifact_consumer_dirs``), and
   each such composite must declare the ``github-token`` input its
   callers are required to pass.

6. **A resilient-pull ladder can exhaust inside the job that runs it.**
   Invariant 3 applied to the other ladder in the tree. ``timeout`` on a
   single ``docker pull`` bounds one attempt; it says nothing about the
   ladder, which pays that bound twice per attempt (Docker Hub, then the
   mirror) plus doubling backoff. Sized wrong, the ladder outlasts its job,
   the runner reaps it mid-retry, and a retryable registry stall surfaces as
   an opaque ``cancelled`` job with no diagnosis -- precisely the outcome
   the ladder exists to turn INTO a named failure, reintroduced one level up.

   The worst case is ``2 x attempts x pull-timeout-seconds + 10 x (2^(n-1) -
   1)``, resolved per call site from the step's ``with:`` over the action's
   declared defaults, and compared against the job's ``timeout-minutes``. A
   ``${{ }}`` expression resolves to the default rather than being skipped,
   so a parameterised call site is still judged. Costs propagate through the
   composite call graph by fixpoint, because a ladder nested a level deep is
   still paid by the job's budget.

7. **A resilient-pull ladder's local tag reaches only a consumer that can
   resolve one.** The ladder's output names an image in the runner's daemon
   and nowhere else, so an action that pulls its image input unconditionally
   (``setup-qemu-action`` does) is not merely bypassing the ladder: the name
   resolves to no repository, and the step becomes impossible rather than
   merely unprotected. Only a static check catches that, because such steps
   are typically gated off ``pull_request`` and so never run before main.

   A tag may be consumed from a ``run:`` step that follows its producer, or by
   an action input in ``_LOCAL_TAG_CONSUMERS``. Matching is by whole token and
   exact value, never substring, so one tag cannot vouch for another that
   merely contains it. A tag nothing consumes is flagged (the ladder then
   guards nothing), and a ``${{ }}``-valued tag is flagged outright, since
   matching unevaluated text would let an aliased spelling pass silently.

8. **Every Dockerfile reference BuildKit resolves itself is digest-pinned.**
   The buildx driver mirrors ``docker.io`` through ``mirror.gcr.io`` so a
   Docker Hub stall cannot kill a build after the pull ladder already saved
   the driver boot. That substitution is safe only because the ``# syntax=``
   frontend, every ``FROM``, and every ``COPY --from=`` carry a digest: a
   mirror serving different content then fails verification instead of being
   trusted. The property lives in ``docker/**/Dockerfile``, not in the
   workflow that enables the mirror, so nothing else can enforce it, and a new
   Dockerfile with a tag-only base would silently reopen the gap. Build stages
   and ``${...}`` build args are exempt: neither resolves against a registry.

The enforced set is deliberately narrow: the external upload/OIDC actions
that lack their own retry AND sit on an important / required path. Other
externally-dependent actions are excluded by design (see ``_EXCLUDED``)
because they retry internally or are non-blocking feature-advisory.

Invariants 1-2 scan ``.github/workflows/``; invariants 3-6 also scan
``.github/actions/*/action.yml``, because composite actions host every
``retry_cmd.sh`` call site, can bypass a wrapper themselves, and are the
transitive artifact consumers and ladder hosts invariants 5-6 resolve
through.

This is a no-baseline gate: the convention passes clean from day one. If
it flags an existing workflow, fix the workflow -- do NOT add a baseline.

Usage::

    python scripts/check_ci_workflow_resilience.py <file>...   # pre-commit
    python scripts/check_ci_workflow_resilience.py --scan-all  # CI / manual
"""

import argparse
import re
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Final

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS_ROOT = _REPO_ROOT / ".github" / "workflows"
_ACTIONS_ROOT = _REPO_ROOT / ".github" / "actions"

_RETRY_HELPER: Final[str] = "retry_cmd.sh"
_DEADLINE_VAR: Final[str] = "RETRY_CMD_DEADLINE"

_CHECKOUT_ACTION: Final[str] = "actions/checkout"
_CHECKOUT_WRAPPER_DIR: Final[str] = ".github/actions/checkout"

_PULL_ACTION_DIR: Final[str] = ".github/actions/docker-pull-resilient"
_ATTEMPTS_INPUT: Final[str] = "attempts"
_PULL_TIMEOUT_INPUT: Final[str] = "pull-timeout-seconds"
_LOCAL_TAG_INPUT: Final[str] = "local-tag"
_EXPRESSION_MARKER: Final[str] = "${{"

_DOCKER_DIR: Final[str] = "docker"
_DOCKERFILE_NAME: Final[str] = "Dockerfile"
_SYNTAX_PREFIX: Final[str] = "# syntax="
_COPY_FROM_FLAG: Final[str] = "--from="
# Both `${VAR}` and the equally legal bare `$VAR` spelling.
_BUILD_ARG_PREFIX: Final[str] = "$"
_DIGEST_MARKER: Final[str] = "@sha256:"
# ``FROM <ref> AS <name>`` is the shortest aliased form.
_FROM_ALIAS_WORDS: Final[int] = 3
# Delimiters around an image reference in either a shell body or a `with:`
# value: `driver-opts: image=<tag>` and a `${{ ... && 'image=<tag>' }}`
# expression both have to yield the bare tag as one token.
_TOKEN_SPLIT: Final[re.Pattern[str]] = re.compile(r"""[\s;|&()<>=,"'`]+""")

# ``uses:`` inputs that resolve against the local daemon: buildx's
# docker-container driver attempts a registry pull but falls back to an
# already-present local image ("pulling failed, using local image ..."). An
# unlisted input is assumed to pull outright, which is fatal for a tag naming
# no registry repository.
_LOCAL_TAG_CONSUMERS: Final[dict[str, frozenset[str]]] = {
    "docker/setup-buildx-action": frozenset({"driver-opts"}),
}
# Each attempt pays the per-pull bound twice: Docker Hub, then the mirror.
_REGISTRIES_PER_ATTEMPT: Final[int] = 2
_BACKOFF_BASE_SECONDS: Final[int] = 10
_SECONDS_PER_MINUTE: Final[int] = 60

_DOWNLOAD_ACTION: Final[str] = "actions/download-artifact"
_DOWNLOAD_WRAPPER_DIR: Final[str] = ".github/actions/download-artifact"
_TOKEN_INPUT: Final[str] = "github-token"  # noqa: S105 -- an input name, not a secret
_ACTIONS_SCOPE: Final[str] = "actions"
# ``write`` subsumes ``read``; the two blanket string forms are the only
# other shapes that grant the scope.
_ACTIONS_READ_VALUES: Final[frozenset[str]] = frozenset({"read", "write"})
_BLANKET_READ_PERMISSIONS: Final[frozenset[str]] = frozenset({"read-all", "write-all"})

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


def _is_checkout_action(uses: str) -> bool:
    """Return True when ``uses`` refers to a step that checks the repo out.

    Three reference forms count: the upstream ``actions/checkout``, the in-repo
    retry wrapper referenced locally (``./.github/actions/checkout``), and the
    same wrapper referenced fork-qualified
    (``<owner>/<repo>/.github/actions/checkout@<sha>``, the form a job must use
    when the workspace is not yet populated and which nearly every job here
    does use).

    Matched on the resolved action id rather than by substring, so a NEIGHBOUR
    of the wrapper (``./.github/actions/checkout-legacy``) is not mistaken for
    one: its absent ``sparse-checkout`` would otherwise be folded in as full
    coverage and suppress every genuine violation in the rest of the job.
    """
    action = _action_id(uses).removeprefix("./").rstrip("/")
    if action == _CHECKOUT_ACTION:
        return True
    # Bare equality catches the local form; the ``/``-anchored suffix catches
    # the fork-qualified one without letting a same-named directory deeper in
    # some unrelated path match by accident.
    return action == _CHECKOUT_WRAPPER_DIR or action.endswith(
        f"/{_CHECKOUT_WRAPPER_DIR}"
    )


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


def _normalise_pattern(entry: object) -> str:
    """Return a sparse-checkout entry as a bare repo-relative directory prefix."""
    return str(entry).strip().removeprefix("./").strip("/")


def _sparse_prefixes(sparse: object) -> list[str] | None:
    """Return the path prefixes a sparse-checkout spec materialises.

    ``None`` means "not sparse at all": an absent or blank spec is a full
    checkout, which covers every path.

    ``actions/checkout`` accepts the spec in two YAML shapes -- a block
    scalar (``sparse-checkout: |``) and a sequence (``sparse-checkout: [a,
    b]``) -- and treating an unrecognised shape as "no spec" would read a
    genuinely sparse checkout as a full one, passing the invariant on a job
    that cannot resolve its action. Both shapes are parsed for that reason.
    A negation pattern (non-cone mode's ``!path``) subtracts rather than
    adds, so it contributes no prefix.

    Args:
        sparse: The step's ``sparse-checkout`` value.

    Returns:
        The covered prefixes, or ``None`` when the checkout is not sparse.
    """
    if sparse is None:
        return None
    entries = sparse if isinstance(sparse, list) else str(sparse).splitlines()
    prefixes = [
        pattern
        for pattern in (_normalise_pattern(entry) for entry in entries)
        if pattern and not pattern.startswith("!")
    ]
    return prefixes or None


def _covered_by(path: str, prefix: str) -> bool:
    """Return True when *prefix* is *path* itself or one of its ancestors.

    Segment-aware, so ``.github/actions-old`` is not read as covered by a
    checkout of ``.github/actions``.
    """
    return path == prefix or path.startswith(f"{prefix}/")


class _CheckoutState:
    """What a job's checkouts have placed on disk so far.

    A ``uses: ./...`` step resolves from the workspace, so it only works if
    an EARLIER step in the same job checked the action out. Tracking this in
    step order is the whole point: a checkout later in the job is too late.
    """

    def __init__(self) -> None:
        self.full = False
        self.sparse_prefixes: set[str] = set()

    def record(self, step: dict[str, object]) -> None:
        """Fold a checkout step's coverage into the state."""
        with_ = step.get("with")
        sparse = with_.get("sparse-checkout") if isinstance(with_, dict) else None
        prefixes = _sparse_prefixes(sparse)
        if prefixes is None:
            self.full = True
            return
        self.sparse_prefixes.update(prefixes)

    def resolves(self, action_dir: str) -> bool:
        """Return True when *action_dir* is on disk by this point in the job."""
        return self.full or any(
            _covered_by(action_dir, prefix) for prefix in self.sparse_prefixes
        )


def _check_local_action_resolvable(
    job_name: str, uses: str, checkout: _CheckoutState
) -> list[str]:
    """Return a violation when a local action cannot be resolved where used."""
    action_dir = uses.removeprefix("./").rstrip("/")
    if checkout.resolves(action_dir):
        return []
    reason = (
        "the job never checks out"
        if not checkout.sparse_prefixes
        else "the job's sparse checkout excludes that path"
    )
    return [
        (
            f"job '{job_name}': uses local action `{uses}` but {reason}, so the"
            " runner cannot find its action.yml. Check out the path first, or"
            " call the upstream action directly."
        )
    ]


def _check_wrapped_action(
    context: str, rel_path: str, uses: str, checkout: _CheckoutState | None
) -> list[str]:
    """Return violations for a step bypassing a wrapper's retry ladder.

    Reaching upstream is legitimate exactly where the wrapper cannot be
    reached: a job that never checks out, or checks out sparsely without the
    action's path, physically cannot run a local composite. Deciding that
    structurally beats an allowlist, which would go stale the moment a job
    gained or lost its checkout.

    Args:
        context: Human-readable location (job or composite-action step).
        rel_path: Repo-relative path of the file being scanned.
        uses: The step's ``uses`` value.
        checkout: Checkout coverage so far, or ``None`` inside a composite
            action (whose caller necessarily checked out to fetch it).

    Returns:
        One message when the step bypasses a reachable wrapper, else empty.
    """
    action = _action_id(uses)
    wrapper = _WRAPPED_ACTIONS.get(action)
    if wrapper is None or rel_path == wrapper:
        return []
    wrapper_dir = Path(wrapper).parent.as_posix()
    if checkout is not None and not checkout.resolves(wrapper_dir):
        return []
    return [
        (
            f"{context}: uses `{action}` directly; call `./{wrapper_dir}`"
            " instead so the retry ladder cannot be bypassed"
        )
    ]


def _local_action_dir(uses: str) -> str | None:
    """Return the repo-relative action directory a ``uses:`` ref names.

    Both in-repo reference forms resolve: the local ``./.github/actions/x``
    and the fork-qualified ``<owner>/<repo>/.github/actions/x@<sha>`` a job
    must use before its workspace is populated. ``None`` for anything that is
    not an in-repo action.
    """
    action = _action_id(uses).removeprefix("./").rstrip("/")
    marker = f"{_ACTIONS_ROOT.parent.name}/{_ACTIONS_ROOT.name}/"
    if action.startswith(marker):
        return action
    index = action.find(f"/{marker}")
    return action[index + 1 :] if index != -1 else None


def _iter_action_files() -> list[Path]:
    """Return every composite action definition under ``.github/actions/``."""
    if not _ACTIONS_ROOT.exists():
        return []
    return sorted(
        path
        for name in ("action.yml", "action.yaml")
        for path in _ACTIONS_ROOT.rglob(name)
    )


def _composite_uses(data: dict[str, object]) -> list[str]:
    """Return the ``uses:`` values of a composite action's steps."""
    runs = data.get("runs")
    if not isinstance(runs, dict):
        return []
    steps = runs.get("steps")
    if not isinstance(steps, list):
        return []
    return [
        str(step["uses"])
        for step in steps
        if isinstance(step, dict) and isinstance(step.get("uses"), str)
    ]


def _load_yaml_mapping(path: Path) -> dict[str, object] | None:
    """Parse *path* as YAML, returning ``None`` unless it is a mapping."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError, yaml.YAMLError, UnicodeDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _artifact_consumer_dirs() -> frozenset[str]:
    """Return every local action directory that reaches an artifact download.

    A composite consumes artifacts when it uses the upstream action, the
    in-repo wrapper, or another composite that does, so the set is closed
    under "calls a consumer" by fixpoint. A hand-maintained list would lose
    that inheritance the first time a composite gained a download, and the
    caller whose ``permissions:`` block is the thing that actually has to
    change is always a level or two up from the download itself.
    """
    calls: dict[str, set[str]] = {}
    consumers: set[str] = {_DOWNLOAD_WRAPPER_DIR}
    for path in _iter_action_files():
        data = _load_yaml_mapping(path)
        if data is None:
            continue
        directory = _relative_path(path.parent)
        used = _composite_uses(data)
        if any(_action_id(ref) == _DOWNLOAD_ACTION for ref in used):
            consumers.add(directory)
        calls[directory] = {
            local
            for local in (_local_action_dir(ref) for ref in used)
            if local is not None
        }
    settled = False
    while not settled:
        settled = True
        for directory, targets in calls.items():
            if directory not in consumers and targets & consumers:
                consumers.add(directory)
                settled = False
    return frozenset(consumers)


def _consumes_artifacts(uses: str, consumers: frozenset[str]) -> bool:
    """Return True when a step downloads artifacts, directly or transitively."""
    if _action_id(uses) == _DOWNLOAD_ACTION:
        return True
    directory = _local_action_dir(uses)
    return directory is not None and directory in consumers


def _step_passes_token(step: dict[str, object]) -> bool:
    """Return True when the step passes a non-empty ``github-token`` input."""
    with_ = step.get("with")
    if not isinstance(with_, dict):
        return False
    value = with_.get(_TOKEN_INPUT)
    return isinstance(value, str) and bool(value.strip())


def _grants_actions_read(permissions: object) -> bool:
    """Return True when a ``permissions:`` value grants the ``actions`` scope.

    Covers the blanket string forms as well as the per-scope mapping. An
    absent or empty block grants nothing: GitHub's repository-level default
    is not knowable from the workflow, so it cannot be assumed to include a
    scope this gate exists to guarantee.
    """
    if isinstance(permissions, str):
        return permissions.strip() in _BLANKET_READ_PERMISSIONS
    if not isinstance(permissions, dict):
        return False
    value = permissions.get(_ACTIONS_SCOPE)
    return isinstance(value, str) and value.strip() in _ACTIONS_READ_VALUES


def _step_label(step: dict[str, object], index: int) -> str:
    """Return the step's ``name``, or a 1-based positional fallback."""
    name = step.get("name")
    return str(name) if isinstance(name, str) else f"step {index + 1}"


def _check_artifact_downloads(
    job_name: str,
    job: dict[str, object],
    workflow_permissions: object,
    consumers: frozenset[str],
) -> list[str]:
    """Return violations for an artifact consumer missing its token or scope.

    A job-level ``permissions:`` block REPLACES the workflow-level one rather
    than merging into it, so the key's presence decides which block is
    effective: a job that declares other scopes and omits ``actions`` has
    genuinely dropped it, however generous the workflow default is.

    Args:
        job_name: Job key, for the message.
        job: The job mapping.
        workflow_permissions: The workflow-level ``permissions:`` value.
        consumers: Local action directories that reach a download.

    Returns:
        Violation messages, empty when the job consumes no artifacts or is
        correctly wired.
    """
    consuming = [
        (index, step)
        for index, step in enumerate(_job_steps(job))
        if isinstance(step.get("uses"), str)
        and _consumes_artifacts(str(step["uses"]), consumers)
    ]
    if not consuming:
        return []
    violations = [
        (
            f"job '{job_name}': '{_step_label(step, index)}' consumes artifacts"
            f" without passing `{_TOKEN_INPUT}`, so it uses the runtime token"
            " and cannot see artifacts uploaded by an earlier run attempt"
            " (re-running this job would 404)"
        )
        for index, step in consuming
        if not _step_passes_token(step)
    ]
    effective = job.get("permissions", workflow_permissions)
    if not _grants_actions_read(effective):
        violations.append(
            f"job '{job_name}': consumes artifacts but does not grant"
            f" `{_ACTIONS_SCOPE}: read`, which the API download path its"
            f" `{_TOKEN_INPUT}` selects requires"
        )
    return violations


def _ladder_worst_case_seconds(attempts: int, pull_timeout: int) -> int:
    """Return the longest the resilient-pull ladder can run before giving up.

    Every attempt pays the per-pull bound once per registry, and the backoff
    doubles from 10s after each attempt except the last.
    """
    pulls = _REGISTRIES_PER_ATTEMPT * attempts * pull_timeout
    # Shift rather than ``2 ** n``: the doubling sum is exact in ints, and
    # ``**`` widens to Any because a negative exponent would yield a float.
    backoff = _BACKOFF_BASE_SECONDS * ((1 << (attempts - 1)) - 1)
    return pulls + backoff


def _positive_int(value: object, fallback: int) -> int:
    """Coerce a YAML scalar to a positive int, falling back when it cannot be.

    A ``${{ }}`` expression is only known at run time, so the action's own
    default is the only defensible static assumption. Resolving toward the
    default keeps the estimate honest rather than optimistic: a caller that
    parameterises the ladder owns the budget it passes.
    """
    try:
        parsed = int(str(value).strip())
    except TypeError, ValueError:
        return fallback
    return parsed if parsed > 0 else fallback


def _pull_action_defaults() -> tuple[int, int]:
    """Return the resilient-pull action's declared ``(attempts, timeout)``."""
    attempts, timeout = 1, 1
    definition = _REPO_ROOT / _PULL_ACTION_DIR / "action.yml"
    data = _load_yaml_mapping(definition)
    inputs = data.get("inputs") if data else None
    if not isinstance(inputs, dict):
        return attempts, timeout
    for key, target in (
        (_ATTEMPTS_INPUT, "attempts"),
        (_PULL_TIMEOUT_INPUT, "timeout"),
    ):
        spec = inputs.get(key)
        if not isinstance(spec, dict):
            continue
        value = _positive_int(spec.get("default"), 1)
        if target == "attempts":
            attempts = value
        else:
            timeout = value
    return attempts, timeout


def _step_ladder_seconds(
    step: dict[str, object], defaults: tuple[int, int]
) -> int | None:
    """Return a step's worst-case ladder duration, or ``None`` if not one."""
    uses = step.get("uses")
    if not isinstance(uses, str) or _local_action_dir(uses) != _PULL_ACTION_DIR:
        return None
    with_ = step.get("with")
    overrides = with_ if isinstance(with_, dict) else {}
    default_attempts, default_timeout = defaults
    return _ladder_worst_case_seconds(
        _positive_int(overrides.get(_ATTEMPTS_INPUT), default_attempts),
        _positive_int(overrides.get(_PULL_TIMEOUT_INPUT), default_timeout),
    )


def _pull_ladder_costs(defaults: tuple[int, int]) -> dict[str, int]:
    """Return each local action's worst-case ladder duration, by directory.

    Fixpoint over the call graph, the same shape as the artifact closure: a
    composite's cost is the worst of its own call sites and of any composite
    it calls, because a job reaching it can pay either. Without this, a ladder
    nested one composite deep would be invisible to the job whose
    ``timeout-minutes`` actually has to accommodate it.
    """
    direct: dict[str, int] = {}
    calls: dict[str, set[str]] = {}
    for path in _iter_action_files():
        data = _load_yaml_mapping(path)
        if data is None:
            continue
        directory = _relative_path(path.parent)
        runs = data.get("runs")
        steps = runs.get("steps") if isinstance(runs, dict) else None
        if not isinstance(steps, list):
            continue
        costs = [
            seconds
            for step in steps
            if isinstance(step, dict)
            and (seconds := _step_ladder_seconds(step, defaults)) is not None
        ]
        if costs:
            direct[directory] = max(costs)
        calls[directory] = {
            local
            for local in (
                _local_action_dir(str(step["uses"]))
                for step in steps
                if isinstance(step, dict) and isinstance(step.get("uses"), str)
            )
            if local is not None
        }
    settled = False
    while not settled:
        settled = True
        for directory, targets in calls.items():
            reachable = [direct[t] for t in targets if t in direct]
            if not reachable:
                continue
            worst = max(reachable)
            if worst > direct.get(directory, 0):
                direct[directory] = worst
                settled = False
    return direct


def _check_ladder_budget(
    job_name: str,
    job: dict[str, object],
    costs: dict[str, int],
    defaults: tuple[int, int],
) -> list[str]:
    """Return a violation when a pull ladder cannot exhaust inside its job.

    Bounding one pull is not enough: if the ladder's worst case outlasts the
    job, the runner reaps the job mid-retry and a retryable registry stall
    surfaces as an opaque cancellation with no diagnosis, which is the exact
    outcome the ladder exists to convert into a named failure. Same rule as
    invariant 3, applied to the other ladder in the tree.

    Args:
        job_name: Job key, for the message.
        job: The job mapping.
        costs: Worst-case ladder duration per local action directory.
        defaults: The pull action's declared ``(attempts, timeout)``.

    Returns:
        One message when the job's budget cannot contain its worst ladder.
    """
    worst = 0
    for step in _job_steps(job):
        direct = _step_ladder_seconds(step, defaults)
        if direct is not None:
            worst = max(worst, direct)
            continue
        uses = step.get("uses")
        if isinstance(uses, str):
            nested = _local_action_dir(uses)
            if nested is not None and nested in costs:
                worst = max(worst, costs[nested])
    if not worst:
        return []
    budget = _positive_int(job.get(_TIMEOUT_KEY), 0) * _SECONDS_PER_MINUTE
    if budget > worst:
        return []
    return [
        (
            f"job '{job_name}': the resilient-pull ladder can run up to {worst}s"
            f" but the job is budgeted {budget}s, so the runner reaps it"
            " mid-retry and a registry stall surfaces as an opaque cancelled"
            f" job. Raise {_TIMEOUT_KEY}, or lower `{_ATTEMPTS_INPUT}` /"
            f" `{_PULL_TIMEOUT_INPUT}` at the call site."
        )
    ]


def _declares_token_input(data: dict[str, object]) -> bool:
    """Return True when a composite action declares a ``github-token`` input.

    A composite cannot declare ``permissions:`` -- that is its caller's job --
    but it must accept the token its caller is required to pass, else the
    caller's compliance stops at the boundary and the download inside falls
    back to the runtime token.
    """
    inputs = data.get("inputs")
    return isinstance(inputs, dict) and _TOKEN_INPUT in inputs


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


def _is_pull_producer(step: dict[str, object]) -> bool:
    """Return True when the step is a resilient-pull call."""
    uses = step.get("uses")
    return isinstance(uses, str) and _local_action_dir(uses) == _PULL_ACTION_DIR


def _produced_local_tags(steps: Sequence[object]) -> dict[str, int]:
    """Return each ``local-tag`` the ladder emits, by FIRST producing index.

    First rather than last: with two producers of one tag the image is in the
    daemon from the earlier one onward, so that is the index a consumer has to
    follow to be valid.
    """
    tags: dict[str, int] = {}
    for index, step in enumerate(steps):
        if not isinstance(step, dict) or not _is_pull_producer(step):
            continue
        with_ = step.get("with")
        tag = with_.get(_LOCAL_TAG_INPUT) if isinstance(with_, dict) else None
        if isinstance(tag, str) and tag.strip():
            tags.setdefault(tag.strip(), index)
    return tags


def _mentions_tag(text: str, tag: str) -> bool:
    """Return True when *text* references *tag* as a whole token.

    Substring matching would let one tag vouch for another whose name merely
    contains it (``:qemu-v9.2.2`` inside ``:qemu-v9.2.20``), masking a real
    orphan and inventing a violation against correct wiring.
    """
    return any(token == tag for token in _TOKEN_SPLIT.split(text))


def _consumes_in_shell(step: dict[str, object], tag: str) -> bool:
    """Return True when a ``run:`` step resolves *tag* itself.

    ``env:`` counts only on a ``run:`` step: on a ``uses:`` step it feeds the
    action's process, not a shell that could resolve a local image.
    """
    if "run" not in step:
        return False
    if _mentions_tag(str(step.get("run", "")), tag):
        return True
    env = step.get("env")
    if not isinstance(env, dict):
        return False
    return any(_mentions_tag(str(value), tag) for value in env.values())


def _step_tag_violations(
    prefix: str,
    step: dict[str, object],
    index: int,
    tags: dict[str, int],
    consumed: set[str],
) -> list[str]:
    """Return violations for one step's use of any produced local tag.

    Args:
        prefix: Message prefix, already terminated, or empty.
        step: The step mapping.
        index: The step's position, judged against each tag's producer.
        tags: Produced tag to first producing index.
        consumed: Mutated with every tag this step resolves.

    Returns:
        One message per tag handed to an input that pulls.
    """
    uses = step.get("uses")
    action = _action_id(uses) if isinstance(uses, str) else ""
    allowed = _LOCAL_TAG_CONSUMERS.get(action, frozenset())
    raw_with = step.get("with")
    with_ = raw_with if isinstance(raw_with, dict) else {}
    violations: list[str] = []
    for tag, producer in tags.items():
        # A reference before the producer names an image the daemon does not
        # hold yet, so it cannot vouch for the ladder.
        if index <= producer:
            continue
        if _consumes_in_shell(step, tag):
            consumed.add(tag)
        for name, value in with_.items():
            if not _mentions_tag(str(value), tag):
                continue
            consumed.add(tag)
            if str(name) in allowed:
                continue
            violations.append(
                f"{prefix}'{_step_label(step, index)}' passes local tag"
                f" '{tag}' to `{action}` input `{name}`, which resolves the"
                " reference against a registry, where a local-only tag names"
                " no repository. Consume it from a `run:` step, or add the"
                " input to _LOCAL_TAG_CONSUMERS once upstream is confirmed"
                " to resolve locally."
            )
    return violations


def _check_local_tag_consumers(context: str, steps: Sequence[object]) -> list[str]:
    """Return violations where a ladder's local tag reaches a pulling consumer.

    The ladder's output exists only in the runner's daemon, so an action that
    pulls its image input does not merely bypass the ladder: the name resolves
    to no repository and the step cannot succeed at all.

    Args:
        context: Job label, or empty for a composite (the printer already
            names the file).
        steps: The step list to walk.

    Returns:
        A message per pulling consumer, per unresolvable tag, and per tag
        nothing consumes.
    """
    tags = _produced_local_tags(steps)
    if not tags:
        return []
    prefix = f"{context}: " if context else ""
    # Fail closed on an expression: matching it to a consumer means comparing
    # unevaluated text, so an aliased spelling would silently pass.
    violations = [
        f"{prefix}pull ladder emits local tag '{tag}' as an unresolved"
        " expression, so no consumer can be matched statically. Pass a literal."
        for tag in tags
        if _EXPRESSION_MARKER in tag
    ]
    consumed: set[str] = set()
    for index, step in enumerate(steps):
        if not isinstance(step, dict) or _is_pull_producer(step):
            continue
        violations.extend(_step_tag_violations(prefix, step, index, tags, consumed))
    violations.extend(
        f"{prefix}pull ladder emits local tag '{tag}' that nothing consumes,"
        " so the ladder guards no pull."
        for tag in tags
        if tag not in consumed and _EXPRESSION_MARKER not in tag
    )
    return violations


def _scan_composite_action(
    data: dict[str, object], rel_path: str, consumers: frozenset[str]
) -> list[str]:
    """Return violations for a composite action file.

    Composite actions have no ``jobs``; their steps hang off ``runs.steps``.
    Invariants 3-5 apply -- an action cannot declare ``timeout-minutes``, and
    the enforced upload actions are not used from one.

    Args:
        data: The parsed ``action.yml``.
        rel_path: Repo-relative path, so a wrapper can exempt itself.
        consumers: Local action directories that reach an artifact download.

    Returns:
        Violation messages, empty when the action is compliant.
    """
    runs = data.get("runs")
    if not isinstance(runs, dict):
        return []
    steps = runs.get("steps")
    if not isinstance(steps, list):
        return []
    violations: list[str] = _check_local_tag_consumers("", steps)
    consumes = False
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        label = _step_label(step, index)
        violations.extend(_check_retry_deadlines(f"'{label}'", step))
        uses = step.get("uses")
        if not isinstance(uses, str):
            continue
        violations.extend(_check_wrapped_action(f"'{label}'", rel_path, uses, None))
        if not _consumes_artifacts(uses, consumers):
            continue
        consumes = True
        if not _step_passes_token(step):
            violations.append(
                f"'{label}': consumes artifacts without passing"
                f" `{_TOKEN_INPUT}`, dropping the caller's token at this"
                " boundary"
            )
    if consumes and not _declares_token_input(data):
        violations.append(
            f"action consumes artifacts but declares no `{_TOKEN_INPUT}` input,"
            " so its callers have no way to reach the API download path that"
            " sees a previous attempt's artifacts"
        )
    return violations


def _check_job_steps(job_name: str, rel_path: str, job: dict[str, object]) -> list[str]:
    """Return per-step violations for one job, walked in execution order.

    Order matters: checkout coverage accumulates as the job runs, so a local
    action is judged against what is on disk at ITS point, not at the end.

    Args:
        job_name: Job key, for the message.
        rel_path: Repo-relative path of the workflow file.
        job: The job mapping.

    Returns:
        Violation messages, empty when the job is compliant.
    """
    steps = _job_steps(job)
    violations: list[str] = _check_local_tag_consumers(f"job '{job_name}'", steps)
    checkout = _CheckoutState()
    for step in steps:
        violations.extend(_check_retry_deadlines(f"job '{job_name}'", step))
        uses = step.get("uses")
        if not isinstance(uses, str):
            continue
        is_local = uses.startswith("./")
        # A LOCAL checkout wrapper needs a checkout of its own to exist, so
        # resolvability is judged before any coverage is folded in -- ordering
        # the two the other way would let the step vouch for itself.
        if is_local:
            violations.extend(_check_local_action_resolvable(job_name, uses, checkout))
        if _is_checkout_action(uses):
            checkout.record(step)
        elif not is_local:
            violations.extend(
                _check_wrapped_action(f"job '{job_name}'", rel_path, uses, checkout)
            )
    return violations


def _relative_path(path: Path) -> str:
    """Return *path* relative to the repo root, or absolute if outside it."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def _scan_file(path: Path, consumers: frozenset[str] | None = None) -> list[str]:
    """Return all violation messages for one workflow file.

    Args:
        path: The workflow or composite-action file to scan.
        consumers: Local action directories that reach an artifact download.
            Computed on demand when omitted; ``_scan_paths`` passes the one
            it resolved so a whole-tree run walks the actions tree once.

    Returns:
        Violation messages, empty when the file is compliant.
    """
    rel_path = _relative_path(path)
    if consumers is None:
        consumers = _artifact_consumer_dirs()
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
        return _scan_composite_action(data, rel_path, consumers)
    permissions = data.get("permissions")
    pull_defaults = _pull_action_defaults()
    ladder_costs = _pull_ladder_costs(pull_defaults)
    violations: list[str] = []
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        name = str(job_name)
        violations.extend(_check_job_timeout(name, job))
        violations.extend(_check_job_ladders(name, job))
        violations.extend(_check_job_steps(name, rel_path, job))
        violations.extend(_check_artifact_downloads(name, job, permissions, consumers))
        violations.extend(_check_ladder_budget(name, job, ladder_costs, pull_defaults))
    return violations


def _dockerfile_refs(text: str) -> list[tuple[int, str]]:
    """Return ``(line number, image reference)`` for every pinnable reference.

    Build stages and build-arg references (``${VAR}`` or bare ``$VAR``) are
    excluded: neither resolves against a registry, so neither can carry a
    digest.
    """
    stages: set[str] = set()
    refs: list[tuple[int, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if line.startswith(_SYNTAX_PREFIX):
            refs.append((number, line.removeprefix(_SYNTAX_PREFIX).strip()))
            continue
        words = line.split()
        if not words:
            continue
        keyword = words[0].upper()
        if keyword == "FROM":
            operands = [word for word in words[1:] if not word.startswith("--")]
            if not operands:
                continue
            ref = operands[0]
            if len(operands) >= _FROM_ALIAS_WORDS and operands[1].upper() == "AS":
                stages.add(operands[2])
            if ref not in stages and not ref.startswith(_BUILD_ARG_PREFIX):
                refs.append((number, ref))
        elif keyword == "COPY":
            for word in words[1:]:
                if not word.startswith(_COPY_FROM_FLAG):
                    continue
                ref = word.removeprefix(_COPY_FROM_FLAG)
                if (
                    ref not in stages
                    and not ref.isdigit()
                    and not ref.startswith(_BUILD_ARG_PREFIX)
                ):
                    refs.append((number, ref))
    return refs


def _check_dockerfile_digest_pins() -> list[str]:
    """Return violations where a Dockerfile reference is not digest-pinned.

    Invariant 8. The docker.io registry mirror configured on the buildx driver
    is only safe because every reference BuildKit resolves itself is pinned by
    digest: a mirror serving different content then fails verification instead
    of being trusted. That property lives in the Dockerfiles, not the workflow
    that enables the mirror, so nothing else can enforce it.
    """
    root = _REPO_ROOT / _DOCKER_DIR
    if not root.exists():
        return []
    violations: list[str] = []
    for path in sorted(root.rglob(_DOCKERFILE_NAME)):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError, UnicodeDecodeError:
            continue
        violations.extend(
            f"{_relative_path(path)}:{number}: '{ref}' is not digest-pinned."
            " BuildKit resolves this itself, outside the pull ladder, so a"
            " mirrored registry could serve different content undetected."
            " Append `@sha256:<digest>`."
            for number, ref in _dockerfile_refs(text)
            if _DIGEST_MARKER not in ref
        )
    return violations


def _scan_paths(paths: Iterable[Path]) -> int:
    """Scan each path; print violations; return the shell exit code."""
    failed = False
    consumers = _artifact_consumer_dirs()
    for message in _check_dockerfile_digest_pins():
        failed = True
        print(message, file=sys.stderr)
    for path in paths:
        if not path.exists() or path.suffix not in (".yml", ".yaml"):
            continue
        violations = _scan_file(path, consumers)
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
            " RETRY_CMD_DEADLINE sized below its job budget, reach every"
            " wrapped action through its wrapper rather than upstream,"
            " give every artifact-consuming job both `actions: read` and an"
            " explicit github-token, consume every resilient-pull local-tag"
            " from a `run:` step, and digest-pin every Dockerfile reference."
            " To exclude an action deliberately,"
            " add it to _EXCLUDED in scripts/check_ci_workflow_resilience.py"
            " with a reason.",
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
