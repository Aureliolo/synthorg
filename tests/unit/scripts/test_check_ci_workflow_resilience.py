"""Unit tests for ``scripts/check_ci_workflow_resilience.py``.

Loads the script as a module so its private helpers are callable without
spawning subprocesses.

Covers all ten invariants:

* ``timeout-minutes`` required on every job, with the reusable-workflow
  -call exemption (a job whose body is a top-level ``uses:``).
* Enforced external/OIDC upload actions must be in a fail-closed retry
  ladder: a bare single step is flagged, a proper ladder (>=1 attempt
  with ``continue-on-error`` AND >=1 without) passes, and an
  all-soft-failed ladder is flagged. Excluded / unrelated actions are
  ignored.
* Every ``retry_cmd.sh`` call site bounds its ladder with
  ``RETRY_CMD_DEADLINE``, via step ``env:`` or an inline ``VAR=n`` prefix.
* A local action resolves where it is used, and a wrapped upstream action
  is reached only through its wrapper (except where the wrapper is itself
  unreachable). Checkout coverage is tracked in step order and in both
  YAML shapes ``sparse-checkout`` accepts, because a shape the gate fails
  to parse would read as a full checkout and pass a job that cannot
  resolve its action.
* An artifact-consuming job grants ``actions: read`` AND passes an
  explicit ``github-token``, transitively through the composites that
  reach a download, so a re-run can still see the previous attempt's
  artifacts.
* A resilient-pull ladder's worst case fits inside its job's
  ``timeout-minutes``, resolved per call site and through the composite
  call graph, so the runner cannot reap the job mid-retry.
* A ladder's ``local-tag`` reaches only a consumer that resolves a local
  image: a ``run:`` step, or an allowlisted action input. A tag handed to a
  pulling action, or consumed by nothing at all, is flagged.
* Every ``docker/**/Dockerfile`` reference BuildKit resolves itself carries
  a digest, which is what makes the buildx docker.io mirror safe. Build
  stages and ``${...}`` build args are exempt.
* No job floats on a rolling runner alias, with the one documented matrix
  exemption.
* A scheduled workflow routes failure and non-completion to two DIFFERENT
  tracking-issue sinks, judged per watched job, so neither a single broad
  notifier nor a union across jobs can stand in for real coverage. The
  admission predicate is asserted directly as exact tuples rather than
  through the composed message, and the bare ``on:`` key is asserted to
  parse as the YAML-1.1 boolean.
"""

import importlib.util
from functools import cache
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_ci_workflow_resilience.py"


def _load_script_module() -> object:
    """Import the script as a module so private helpers are callable."""
    spec = importlib.util.spec_from_file_location(
        "_check_ci_workflow_resilience",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load_script_module()


# Invariant 11 requires a top-level permissions block on every workflow, so
# every fixture that is not exercising that invariant carries one; otherwise
# each would pick up an unrelated violation and mask the one under test.
_PERMS = "permissions: {}\n"


@cache
def _repo_scan_context() -> tuple[object, object, object, object]:
    """Resolve the four whole-tree lookups ``_scan_file`` would recompute.

    Each walks and parses every composite action under ``.github/actions``.
    Left to the default, this module's ~80 cases redo all four per case,
    which was ~35s of the file's ~43s. The gate itself hoists them once per
    run for the same reason; this mirrors that in the tests rather than
    caching inside the script, where two cases deliberately repoint
    ``_REPO_ROOT`` and a cache would quietly answer for the wrong tree.
    """
    consumers = _MODULE._artifact_consumer_dirs()  # type: ignore[attr-defined]
    sinks = _MODULE._tracking_issue_dirs()  # type: ignore[attr-defined]
    pull_defaults = _MODULE._pull_action_defaults()  # type: ignore[attr-defined]
    ladder_costs = _MODULE._pull_ladder_costs(pull_defaults)  # type: ignore[attr-defined]
    return consumers, sinks, pull_defaults, ladder_costs


def _scan(tmp_path: Path, content: str, *, fresh: bool = False) -> list[str]:
    """Write content to a tmp .yml file and return the violation messages.

    Args:
        tmp_path: Directory to write the workflow into.
        content: The workflow YAML under test.
        fresh: Resolve the whole-tree lookups from scratch instead of reusing
            the cached ones. Required by any case that repoints
            ``_REPO_ROOT`` / ``_ACTIONS_ROOT``: the cache answers for the
            real repository, which is precisely what such a case is not
            asking about.
    """
    target = tmp_path / "wf.yml"
    target.write_text(content, encoding="utf-8")
    context: tuple[object, ...] = () if fresh else _repo_scan_context()
    violations: list[str] = _MODULE._scan_file(  # type: ignore[attr-defined]
        target, *context
    )
    return violations


def _job(
    steps: str,
    *,
    timeout: bool = True,
    minutes: int = 5,
    permissions: str | None = "actions: read",
    top: str = _PERMS,
) -> str:
    """Build a one-job workflow whose ``steps:`` is ``steps``.

    The job grants ``actions: read`` by default so a fixture that happens to
    download an artifact still isolates the invariant under test; pass
    ``permissions=None`` to exercise invariant 5's permission half. ``minutes``
    sets the budget invariant 6 measures a pull ladder against. ``top`` is the
    workflow-level block invariant 11 requires; override it to exercise scope
    inheritance, since a second top-level key would simply replace this one.
    """
    head = f"{top}jobs:\n  a:\n    runs-on: ubuntu-24.04\n"
    if timeout:
        head += f"    timeout-minutes: {minutes}\n"
    if permissions is not None:
        head += f"    permissions:\n      {permissions}\n"
    return f"{head}    steps:\n{steps}"


def _pull(
    *,
    attempts: str | None = None,
    seconds: str | None = None,
    tag: str | None = None,
) -> str:
    """A resilient-pull step, optionally overriding the ladder inputs."""
    step = "      - uses: ./.github/actions/docker-pull-resilient\n"
    overrides = {
        "attempts": attempts,
        "pull-timeout-seconds": seconds,
        "local-tag": tag,
    }
    given = {key: value for key, value in overrides.items() if value is not None}
    if not given:
        return step
    lines = "".join(f'          {key}: "{value}"\n' for key, value in given.items())
    return f"{step}        with:\n{lines}"


def _qemu(image: str) -> str:
    """A setup-qemu-action step, whose `image:` input always pulls."""
    return (
        "      - uses: docker/setup-qemu-action@abc # v4\n"
        "        with:\n"
        f"          image: {image}\n"
    )


def _buildx(driver_opts: str) -> str:
    """A setup-buildx-action step, whose `driver-opts` resolves locally."""
    return (
        "      - uses: docker/setup-buildx-action@abc # v4\n"
        "        with:\n"
        f"          driver-opts: {driver_opts}\n"
    )


def _enforced(*, guard: bool) -> str:
    """A single codecov step, guarded (continue-on-error) or bare."""
    line = "      - uses: codecov/codecov-action@abc # v7\n"
    if guard:
        line += "        continue-on-error: true\n"
    return line


# A well-formed 3-attempt ladder: two guarded attempts, one bare final.
_LADDER = _enforced(guard=True) + _enforced(guard=True) + _enforced(guard=False)

_UNGUARDED = "has an unguarded step"
_SOFT_ONLY = "no fail-closed final attempt"
_NO_DEADLINE = "without RETRY_CMD_DEADLINE"
_NEVER_CHECKS_OUT = "the job never checks out"
_SPARSE_EXCLUDES = "sparse checkout excludes that path"
_BYPASSES_WRAPPER = "so the retry ladder cannot be bypassed"
_NO_TOKEN = "consumes artifacts without passing `github-token`"
_NO_ACTIONS_READ = "does not grant `actions: read`"
_DROPS_TOKEN = "dropping the caller's token at this boundary"
_NO_TOKEN_INPUT = "declares no `github-token` input"
_LADDER_OVERRUNS = "resilient-pull ladder can run up to"
_TAG_PULLED = "which resolves the reference against a registry"
_TAG_ORPHANED = "that nothing consumes"
_TAG_EXPRESSION = "as an unresolved expression"

_TAG = "synthorg-binfmt:qemu-v9.2.2"
_BUILDKIT_TAG = "synthorg-buildkit:buildx-stable-1"


_TOKEN_EXPR = "${{ github.token }}"


def _download(*, wrapper: bool = True, token: str | None = _TOKEN_EXPR) -> str:
    """An artifact-download step, through the wrapper or straight upstream."""
    ref = (
        "./.github/actions/download-artifact"
        if wrapper
        else "actions/download-artifact@abc"
    )
    step = f"      - uses: {ref}\n"
    if token is None:
        return step
    return f'{step}        with:\n          github-token: "{token}"\n'


def _checkout(sparse: str = "", uses: str = "actions/checkout@abc") -> str:
    """A checkout step, optionally carrying a ``sparse-checkout`` spec."""
    step = f"      - uses: {uses}\n"
    if not sparse:
        return step
    return f"{step}        with:\n          sparse-checkout: {sparse}\n"


def _composite(steps: str, *, token_input: bool = True) -> str:
    """Build a composite ``action.yml`` body whose ``runs.steps`` is *steps*.

    Declares a ``github-token`` input by default, which invariant 5 requires
    of any composite that reaches a download; pass ``token_input=False`` to
    exercise that half.
    """
    head = 'inputs:\n  github-token:\n    default: ""\n' if token_input else ""
    return f"{head}runs:\n  using: composite\n  steps:\n{steps}"


class TestTimeout:
    """Every non-reusable job must declare timeout-minutes."""

    def test_missing_timeout_flagged(self, tmp_path: Path) -> None:
        violations = _scan(tmp_path, _job("      - run: true\n", timeout=False))
        assert len(violations) == 1
        assert "no timeout-minutes" in violations[0]
        assert "job 'a'" in violations[0]

    def test_present_timeout_clean(self, tmp_path: Path) -> None:
        assert _scan(tmp_path, _job("      - run: true\n")) == []

    def test_reusable_call_job_exempt(self, tmp_path: Path) -> None:
        # A job whose body is a top-level ``uses:`` string cannot set
        # timeout-minutes; it must not be flagged.
        content = f"{_PERMS}jobs:\n  a:\n    uses: ./.github/workflows/other.yml\n"
        assert _scan(tmp_path, content) == []

    def test_null_uses_still_timeout_checked(self, tmp_path: Path) -> None:
        # A malformed bare ``uses:`` (parses to None) is NOT a real
        # reusable-call job, so it must still be timeout-checked.
        content = f"{_PERMS}jobs:\n  a:\n    uses:\n"
        violations = _scan(tmp_path, content)
        assert len(violations) == 1
        assert "no timeout-minutes" in violations[0]


class TestLadder:
    """Enforced upload/OIDC actions must be in a fail-closed ladder."""

    def test_bare_single_step_flagged(self, tmp_path: Path) -> None:
        violations = _scan(tmp_path, _job(_enforced(guard=False)))
        assert len(violations) == 1
        assert _UNGUARDED in violations[0]
        assert "codecov/codecov-action" in violations[0]
        assert "job 'a'" in violations[0]

    def test_two_bare_steps_flagged(self, tmp_path: Path) -> None:
        # Two un-laddered codecov steps in one job. Neither
        # carries continue-on-error; both are unguarded uploads.
        violations = _scan(
            tmp_path, _job(_enforced(guard=False) + _enforced(guard=False))
        )
        assert len(violations) == 2
        assert all(_UNGUARDED in v for v in violations)

    def test_proper_ladder_clean(self, tmp_path: Path) -> None:
        assert _scan(tmp_path, _job(_LADDER)) == []

    def test_minimal_two_step_ladder_clean(self, tmp_path: Path) -> None:
        # One guarded attempt + one bare final is the minimal valid ladder;
        # a `len(guards) >= 3` style check would wrongly reject it.
        steps = _enforced(guard=True) + _enforced(guard=False)
        assert _scan(tmp_path, _job(steps)) == []

    def test_all_soft_failed_ladder_flagged(self, tmp_path: Path) -> None:
        # Every attempt has continue-on-error -> no fail-closed final
        # attempt -> still a violation (terminal soft-fail is rejected).
        violations = _scan(
            tmp_path, _job(_enforced(guard=True) + _enforced(guard=True))
        )
        assert len(violations) == 1
        assert _SOFT_ONLY in violations[0]
        assert "codecov/codecov-action" in violations[0]

    def test_explicit_false_counts_as_bare(self, tmp_path: Path) -> None:
        # `continue-on-error: false` is non-guarding, exactly like an absent
        # key -> a lone such step is an unguarded upload.
        content = _job(
            "      - uses: codecov/codecov-action@abc\n"
            "        continue-on-error: false\n"
        )
        violations = _scan(tmp_path, content)
        assert len(violations) == 1
        assert _UNGUARDED in violations[0]

    def test_two_ladders_one_malformed_flagged(self, tmp_path: Path) -> None:
        # Two independent codecov ladders in one job (separated by a
        # non-codecov step), the first a bare pair. The state machine must
        # flag the bare pair even though the second ladder is well-formed,
        # which a naive aggregate check over the whole job would not.
        steps = (
            _enforced(guard=False)
            + _enforced(guard=False)
            + "      - name: backoff\n        run: sleep 0\n"
            + _LADDER
        )
        violations = _scan(tmp_path, _job(steps))
        assert len(violations) == 2
        assert all(_UNGUARDED in v for v in violations)

    def test_backoff_steps_do_not_split_a_ladder(self, tmp_path: Path) -> None:
        # A guarded attempt, an interleaved backoff `run:` step, then a bare
        # final attempt is ONE valid ladder, not a bare step.
        steps = (
            _enforced(guard=True)
            + "      - name: backoff\n        run: sleep 0\n"
            + _enforced(guard=False)
        )
        assert _scan(tmp_path, _job(steps)) == []

    def test_codspeed_bare_flagged(self, tmp_path: Path) -> None:
        content = _job("      - uses: CodSpeedHQ/action@abc # v4\n")
        violations = _scan(tmp_path, content)
        assert len(violations) == 1
        assert "CodSpeedHQ/action" in violations[0]

    def test_subpath_prefix_action_flagged(self, tmp_path: Path) -> None:
        # An enforced action used via a subpath ref still matches by prefix.
        content = _job("      - uses: codecov/codecov-action/sub@abc\n")
        violations = _scan(tmp_path, content)
        assert len(violations) == 1
        assert _UNGUARDED in violations[0]

    def test_excluded_action_ignored(self, tmp_path: Path) -> None:
        # deploy-pages is deliberately excluded; a bare step must NOT flag.
        assert _scan(tmp_path, _job("      - uses: actions/deploy-pages@abc\n")) == []

    def test_unrelated_action_ignored(self, tmp_path: Path) -> None:
        assert _scan(tmp_path, _job("      - uses: actions/checkout@abc\n")) == []

    def test_mixed_enforced_and_excluded_clean(self, tmp_path: Path) -> None:
        # A proper codecov ladder plus a bare excluded action: the excluded
        # step must not leak into the enforced-action state machine.
        steps = _LADDER + "      - uses: actions/deploy-pages@abc\n"
        assert _scan(tmp_path, _job(steps)) == []


class TestRetryDeadline:
    """Every retry_cmd.sh call site must bound its ladder in wall-clock."""

    def test_call_site_without_deadline_flagged(self, tmp_path: Path) -> None:
        content = _job("      - run: .github/scripts/retry_cmd.sh 'x' true\n")
        violations = _scan(tmp_path, content)
        assert len(violations) == 1
        assert _NO_DEADLINE in violations[0]

    def test_step_env_deadline_clean(self, tmp_path: Path) -> None:
        content = _job(
            "      - env:\n"
            '          RETRY_CMD_DEADLINE: "60"\n'
            "        run: .github/scripts/retry_cmd.sh 'x' true\n"
        )
        assert _scan(tmp_path, content) == []

    def test_inline_prefix_deadline_clean(self, tmp_path: Path) -> None:
        content = _job(
            "      - run: RETRY_CMD_DEADLINE=60 .github/scripts/retry_cmd.sh 'x' true\n"
        )
        assert _scan(tmp_path, content) == []

    def test_one_bounded_line_does_not_cover_its_sibling(self, tmp_path: Path) -> None:
        # An inline prefix binds to ONE command, so a bounded invocation says
        # nothing about the unbounded one beside it.
        content = _job(
            "      - run: |\n"
            "          RETRY_CMD_DEADLINE=60 .github/scripts/retry_cmd.sh 'a' true\n"
            "          .github/scripts/retry_cmd.sh 'b' true\n"
        )
        violations = _scan(tmp_path, content)
        assert len(violations) == 1
        assert "'b'" in violations[0]

    def test_composite_action_call_site_flagged(self, tmp_path: Path) -> None:
        # Composite actions host most call sites, so they are scanned too.
        content = _composite(
            "    - name: fetch\n      run: .github/scripts/retry_cmd.sh 'x' true\n"
        )
        violations = _scan(tmp_path, content)
        assert len(violations) == 1
        assert _NO_DEADLINE in violations[0]
        assert "'fetch'" in violations[0]


class TestLocalActionResolution:
    """A ``uses: ./...`` step needs an earlier checkout covering its path."""

    def test_no_checkout_flagged(self, tmp_path: Path) -> None:
        violations = _scan(tmp_path, _job("      - uses: ./.github/actions/thing\n"))
        assert len(violations) == 1
        assert _NEVER_CHECKS_OUT in violations[0]

    def test_full_checkout_clean(self, tmp_path: Path) -> None:
        steps = _checkout() + "      - uses: ./.github/actions/thing\n"
        assert _scan(tmp_path, _job(steps)) == []

    def test_checkout_after_the_step_is_too_late(self, tmp_path: Path) -> None:
        # Coverage accrues in step order: a checkout below the step cannot
        # have put the action on disk for it.
        steps = "      - uses: ./.github/actions/thing\n" + _checkout()
        violations = _scan(tmp_path, _job(steps))
        assert len(violations) == 1
        assert _NEVER_CHECKS_OUT in violations[0]

    def test_sparse_block_scalar_covering_path_clean(self, tmp_path: Path) -> None:
        steps = (
            _checkout("|\n            .github/actions\n")
            + "      - uses: ./.github/actions/thing\n"
        )
        assert _scan(tmp_path, _job(steps)) == []

    def test_sparse_block_scalar_excluding_path_flagged(self, tmp_path: Path) -> None:
        steps = (
            _checkout("|\n            web\n")
            + "      - uses: ./.github/actions/thing\n"
        )
        violations = _scan(tmp_path, _job(steps))
        assert len(violations) == 1
        assert _SPARSE_EXCLUDES in violations[0]

    def test_sparse_yaml_list_excluding_path_flagged(self, tmp_path: Path) -> None:
        # The fail-open shape: a sequence is as valid as a block scalar, and
        # treating it as unparseable would read a sparse checkout as a full
        # one and pass a job that cannot resolve its action.
        steps = _checkout("[web, cli]\n") + "      - uses: ./.github/actions/thing\n"
        violations = _scan(tmp_path, _job(steps))
        assert len(violations) == 1
        assert _SPARSE_EXCLUDES in violations[0]

    def test_sparse_yaml_list_covering_path_clean(self, tmp_path: Path) -> None:
        steps = (
            _checkout("[.github/actions, web]\n")
            + "      - uses: ./.github/actions/thing\n"
        )
        assert _scan(tmp_path, _job(steps)) == []

    def test_sparse_ancestor_prefix_covers_descendant(self, tmp_path: Path) -> None:
        steps = (
            _checkout("|\n            .github\n")
            + "      - uses: ./.github/actions/thing\n"
        )
        assert _scan(tmp_path, _job(steps)) == []

    def test_sparse_coverage_does_not_generalise(self, tmp_path: Path) -> None:
        # Checking out one directory is not a full checkout: an action
        # elsewhere in the tree is still absent.
        steps = _checkout("|\n            .github/actions\n") + "      - uses: ./cli\n"
        violations = _scan(tmp_path, _job(steps))
        assert len(violations) == 1
        assert _SPARSE_EXCLUDES in violations[0]

    def test_prefix_match_is_segment_aware(self, tmp_path: Path) -> None:
        steps = (
            _checkout("|\n            .github/actions\n")
            + "      - uses: ./.github/actions-old/thing\n"
        )
        violations = _scan(tmp_path, _job(steps))
        assert len(violations) == 1
        assert _SPARSE_EXCLUDES in violations[0]

    def test_local_checkout_wrapper_cannot_vouch_for_itself(
        self, tmp_path: Path
    ) -> None:
        # A local checkout wrapper is an action like any other: it needs a
        # checkout to exist before it can run.
        violations = _scan(tmp_path, _job("      - uses: ./.github/actions/checkout\n"))
        assert len(violations) == 1
        assert _NEVER_CHECKS_OUT in violations[0]

    def test_fork_qualified_checkout_wrapper_counts_as_a_checkout(
        self, tmp_path: Path
    ) -> None:
        # The form nearly every job here uses: the in-repo retry wrapper
        # referenced fork-qualified, which is what a job MUST do while the
        # workspace is still empty. Failing to recognise it would report every
        # such job as never checking out.
        steps = (
            _checkout(uses="Aureliolo/synthorg/.github/actions/checkout@abc")
            + "      - uses: ./.github/actions/thing\n"
        )
        assert _scan(tmp_path, _job(steps)) == []

    def test_a_wrapper_neighbour_is_not_a_checkout(self, tmp_path: Path) -> None:
        # The over-approximation this predicate exists to prevent: a substring
        # test would accept `checkout-legacy` as a checkout and fold its absent
        # sparse-checkout in as FULL coverage, suppressing every real violation
        # for the rest of the job. The later local action must still be flagged.
        steps = (
            "      - uses: ./.github/actions/checkout-legacy\n"
            "      - uses: ./.github/actions/thing\n"
        )
        violations = _scan(tmp_path, _job(steps))
        assert len(violations) == 2
        assert all(_NEVER_CHECKS_OUT in v for v in violations)

    def test_an_unrelated_action_named_checkout_is_not_a_checkout(
        self, tmp_path: Path
    ) -> None:
        steps = (
            "      - uses: someoneelse/checkout-action@abc\n"
            "      - uses: ./.github/actions/thing\n"
        )
        violations = _scan(tmp_path, _job(steps))
        assert len(violations) == 1
        assert _NEVER_CHECKS_OUT in violations[0]


class TestWrappedAction:
    """A wrapped upstream action is reached only through its wrapper."""

    def test_upstream_with_reachable_wrapper_flagged(self, tmp_path: Path) -> None:
        violations = _scan(tmp_path, _job(_checkout() + _download(wrapper=False)))
        assert len(violations) == 1
        assert _BYPASSES_WRAPPER in violations[0]

    def test_wrapper_call_clean(self, tmp_path: Path) -> None:
        assert _scan(tmp_path, _job(_checkout() + _download())) == []

    def test_upstream_without_a_checkout_allowed(self, tmp_path: Path) -> None:
        # Reaching upstream is legitimate exactly where the wrapper is
        # physically unreachable, which is decided structurally rather than
        # by an allowlist that would go stale on the next checkout change.
        assert _scan(tmp_path, _job(_download(wrapper=False))) == []

    def test_upstream_under_a_sparse_checkout_without_the_wrapper(
        self, tmp_path: Path
    ) -> None:
        steps = _checkout("|\n            web\n") + _download(wrapper=False)
        assert _scan(tmp_path, _job(steps)) == []

    def test_composite_action_bypassing_the_wrapper_flagged(
        self, tmp_path: Path
    ) -> None:
        # A composite's caller necessarily checked out to fetch it, so the
        # wrapper is always reachable from one.
        content = _composite(
            "    - name: fetch\n"
            "      uses: actions/download-artifact@abc\n"
            "      with:\n"
            '        github-token: "${{ inputs.github-token }}"\n'
        )
        violations = _scan(tmp_path, content)
        assert len(violations) == 1
        assert _BYPASSES_WRAPPER in violations[0]


class TestArtifactDownloads:
    """An artifact consumer grants ``actions: read`` AND passes a token.

    Neither half is sufficient alone: the token is what selects the REST API
    download path, and ``actions: read`` is the scope that path requires, so
    a job missing either still 404s on the cross-attempt fetch a re-run of a
    failed consumer depends on.
    """

    def test_missing_token_flagged(self, tmp_path: Path) -> None:
        violations = _scan(tmp_path, _job(_checkout() + _download(token=None)))
        assert len(violations) == 1
        assert _NO_TOKEN in violations[0]

    def test_blank_token_flagged(self, tmp_path: Path) -> None:
        # An empty string is the wrapper's own default and selects the
        # runtime token, so passing the key is not the same as passing a
        # token.
        violations = _scan(tmp_path, _job(_checkout() + _download(token="")))
        assert len(violations) == 1
        assert _NO_TOKEN in violations[0]

    def test_missing_permission_flagged(self, tmp_path: Path) -> None:
        content = _job(_checkout() + _download(), permissions=None)
        violations = _scan(tmp_path, content)
        assert len(violations) == 1
        assert _NO_ACTIONS_READ in violations[0]

    def test_both_halves_present_clean(self, tmp_path: Path) -> None:
        assert _scan(tmp_path, _job(_checkout() + _download())) == []

    def test_non_consuming_job_unaffected(self, tmp_path: Path) -> None:
        assert _scan(tmp_path, _job("      - run: true\n", permissions=None)) == []

    def test_workflow_level_permissions_are_inherited(self, tmp_path: Path) -> None:
        content = _job(
            _checkout() + _download(),
            permissions=None,
            top="permissions:\n  actions: read\n",
        )
        assert _scan(tmp_path, content) == []

    def test_job_block_overrides_rather_than_merges(self, tmp_path: Path) -> None:
        # GitHub replaces the workflow-level block wholesale, so a job that
        # declares any scope and omits `actions` has genuinely dropped it.
        content = _job(
            _checkout() + _download(),
            permissions="contents: read",
            top="permissions:\n  actions: read\n",
        )
        violations = _scan(tmp_path, content)
        assert len(violations) == 1
        assert _NO_ACTIONS_READ in violations[0]

    @pytest.mark.parametrize(
        ("permissions", "granted"),
        [
            ({"actions": "read"}, True),
            ({"actions": "write"}, True),
            ({"actions": "none"}, False),
            ({"contents": "read"}, False),
            ({}, False),
            ("read-all", True),
            ("write-all", True),
            ("none", False),
            (None, False),
        ],
    )
    def test_permission_shapes(self, permissions: object, granted: bool) -> None:
        grants = _MODULE._grants_actions_read  # type: ignore[attr-defined]
        assert grants(permissions) is granted

    def test_transitive_consumer_inherits_the_obligation(self) -> None:
        # A job that never names a download still consumes artifacts when the
        # composite it calls does, and it is that job's permissions block
        # that has to change.
        job = {"steps": [{"uses": "./.github/actions/publish"}]}
        check = _MODULE._check_artifact_downloads  # type: ignore[attr-defined]
        violations = check("a", job, None, frozenset({".github/actions/publish"}))
        assert len(violations) == 2

    def test_fork_qualified_reference_resolves(self) -> None:
        consumes = _MODULE._consumes_artifacts  # type: ignore[attr-defined]
        consumers = frozenset({".github/actions/publish"})
        assert consumes("owner/repo/.github/actions/publish@sha", consumers)
        assert not consumes("owner/repo/.github/actions/other@sha", consumers)

    def test_closure_reaches_composites_that_call_the_wrapper(self) -> None:
        # Only a computed member is worth asserting: the wrapper's own
        # directory is the seed, so its membership holds even if the
        # directory were deleted.
        consumers = _MODULE._artifact_consumer_dirs()  # type: ignore[attr-defined]
        assert ".github/actions/publish-image" in consumers

    def test_closure_follows_a_multi_hop_chain(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The live tree only has depth-1 consumers, so the fixpoint's whole
        # reason for existing goes unexercised by it. A composite that reaches
        # a download only through ANOTHER composite still obliges its caller.
        actions = tmp_path / ".github" / "actions"
        for name, target in (
            ("outer", "./.github/actions/inner"),
            ("inner", "./.github/actions/download-artifact"),
            ("unrelated", "./.github/actions/checkout"),
        ):
            directory = actions / name
            directory.mkdir(parents=True)
            (directory / "action.yml").write_text(
                _composite(f"    - uses: {target}\n"), encoding="utf-8"
            )
        monkeypatch.setattr(_MODULE, "_REPO_ROOT", tmp_path)
        monkeypatch.setattr(_MODULE, "_ACTIONS_ROOT", actions)

        consumers = _MODULE._artifact_consumer_dirs()  # type: ignore[attr-defined]

        assert ".github/actions/inner" in consumers
        assert ".github/actions/outer" in consumers
        assert ".github/actions/unrelated" not in consumers

    def test_composite_dropping_the_token_flagged(self, tmp_path: Path) -> None:
        content = _composite("    - uses: ./.github/actions/download-artifact\n")
        violations = _scan(tmp_path, content)
        assert len(violations) == 1
        assert _DROPS_TOKEN in violations[0]

    def test_composite_without_a_token_input_flagged(self, tmp_path: Path) -> None:
        content = _composite(
            "    - uses: ./.github/actions/download-artifact\n"
            "      with:\n"
            '        github-token: "${{ inputs.github-token }}"\n',
            token_input=False,
        )
        violations = _scan(tmp_path, content)
        assert len(violations) == 1
        assert _NO_TOKEN_INPUT in violations[0]

    def test_composite_not_consuming_needs_no_token_input(self, tmp_path: Path) -> None:
        content = _composite("    - run: true\n      shell: bash\n", token_input=False)
        assert _scan(tmp_path, content) == []


class TestPullLadderBudget:
    """A resilient-pull ladder must be able to exhaust inside its job.

    Bounding one ``docker pull`` is not the same as bounding the ladder: each
    attempt pays that bound twice (Docker Hub, then the mirror) and backoff
    doubles between attempts. Oversized, the runner reaps the job mid-retry
    and the registry stall surfaces as an opaque cancellation, which is the
    outcome the ladder exists to convert into a named failure.
    """

    @pytest.mark.parametrize(
        ("attempts", "timeout", "expected"),
        [
            # 2 x 3 x 60 = 360 pull seconds, + 10 + 20 backoff.
            pytest.param(3, 60, 390, id="defaults"),
            # An oversized ladder: 2 x 5 x 300 = 3000, + 10+20+40+80 backoff.
            pytest.param(5, 300, 3150, id="oversized-ladder"),
            # A single attempt pays no backoff at all.
            pytest.param(1, 60, 120, id="single-attempt"),
        ],
    )
    def test_worst_case_arithmetic(
        self, attempts: int, timeout: int, expected: int
    ) -> None:
        worst = _MODULE._ladder_worst_case_seconds  # type: ignore[attr-defined]
        assert worst(attempts, timeout) == expected

    def test_ladder_outlasting_its_job_flagged(self, tmp_path: Path) -> None:
        # 390s of ladder in a 5-minute (300s) job.
        violations = _scan(tmp_path, _job(_checkout() + _pull(), minutes=5))
        assert len(violations) == 1
        assert _LADDER_OVERRUNS in violations[0]

    def test_ladder_inside_its_job_clean(self, tmp_path: Path) -> None:
        assert _scan(tmp_path, _job(_checkout() + _pull(), minutes=15)) == []

    def test_oversized_ladder_defaults_flagged(self, tmp_path: Path) -> None:
        # 5 attempts x 300s runs 3150s inside the 15-minute schema-validate
        # budget, so the ladder can never exhaust and the job dies opaque
        # instead of naming the failure.
        steps = _checkout() + _pull(attempts="5", seconds="300")
        violations = _scan(tmp_path, _job(steps, minutes=15))
        assert len(violations) == 1
        assert _LADDER_OVERRUNS in violations[0]

    def test_an_expression_resolves_to_the_declared_default(
        self, tmp_path: Path
    ) -> None:
        # An unresolvable value must not skip the check: resolving toward the
        # action's default keeps a parameterised call site judged rather than
        # silently exempt.
        steps = _checkout() + _pull(attempts="${{ inputs.attempts }}")
        assert _scan(tmp_path, _job(steps, minutes=15)) == []
        assert len(_scan(tmp_path, _job(steps, minutes=5))) == 1

    def test_a_nested_ladder_counts_against_the_calling_job(self) -> None:
        # A job that never names the pull action still pays for one reached
        # through a composite, so the closure has to carry the cost up.
        costs = {".github/actions/build-scan-image": 3150}
        job = {
            "timeout-minutes": 15,
            "steps": [{"uses": "./.github/actions/build-scan-image"}],
        }
        check = _MODULE._check_ladder_budget  # type: ignore[attr-defined]
        violations = check("a", job, costs, (3, 60))
        assert len(violations) == 1
        assert _LADDER_OVERRUNS in violations[0]

    def test_a_job_without_any_ladder_is_unaffected(self, tmp_path: Path) -> None:
        assert _scan(tmp_path, _job("      - run: true\n", minutes=1)) == []

    def test_live_defaults_fit_every_caller(self) -> None:
        # The live tree passes --scan-all, but assert the arithmetic directly
        # so a future default bump is caught by a readable failure rather than
        # only by the whole-tree scan.
        attempts, timeout = _MODULE._pull_action_defaults()  # type: ignore[attr-defined]
        worst = _MODULE._ladder_worst_case_seconds(attempts, timeout)  # type: ignore[attr-defined]
        # schema-validate is the tightest caller at 15 minutes.
        assert worst < 15 * 60


class TestLocalTagConsumers:
    """A ladder's local tag must reach only a consumer that resolves one.

    The tag names an image in the runner's daemon and nowhere else, so handing
    it to an action that pulls its image input does not weaken the ladder, it
    makes the step impossible.
    """

    def test_tag_handed_to_a_pulling_action_flagged(self, tmp_path: Path) -> None:
        # The blind spot this closes: the step is gated off pull_request, so
        # this shape is green on every PR yet fails on every push to main.
        steps = _checkout() + _pull(tag=_TAG) + _qemu(_TAG)
        violations = _scan(tmp_path, _job(steps, minutes=15))
        assert len(violations) == 1
        assert _TAG_PULLED in violations[0]

    def test_an_allowlisted_consumer_input_clean(self, tmp_path: Path) -> None:
        steps = (
            _checkout() + _pull(tag=_BUILDKIT_TAG) + _buildx(f"image={_BUILDKIT_TAG}")
        )
        assert _scan(tmp_path, _job(steps, minutes=15)) == []

    def test_an_allowlisted_input_inside_an_expression_clean(
        self, tmp_path: Path
    ) -> None:
        # The live call site wraps the value in a conditional expression, so
        # the tag has to be found as a token rather than as the whole value.
        opts = (
            "${{ github.event_name != 'pull_request'"
            f" && 'image={_BUILDKIT_TAG}' || '' }}"
        )
        steps = _checkout() + _pull(tag=_BUILDKIT_TAG) + _buildx(opts)
        assert _scan(tmp_path, _job(steps, minutes=15)) == []

    def test_the_same_action_on_a_different_input_is_flagged(
        self, tmp_path: Path
    ) -> None:
        # The allowlist is per input, not per action: driver-opts resolves
        # locally, an image input on the same action would not.
        steps = (
            _checkout()
            + _pull(tag=_TAG)
            + "      - uses: docker/setup-buildx-action@abc # v4\n"
            "        with:\n"
            f"          image: {_TAG}\n"
        )
        violations = _scan(tmp_path, _job(steps, minutes=15))
        assert len(violations) == 1
        assert _TAG_PULLED in violations[0]

    def test_consumption_from_a_run_step_clean(self, tmp_path: Path) -> None:
        steps = (
            _checkout()
            + _pull(tag=_TAG)
            + f"      - run: docker run --rm --privileged {_TAG} --install arm64\n"
        )
        assert _scan(tmp_path, _job(steps, minutes=15)) == []

    def test_consumption_through_a_run_step_env_clean(self, tmp_path: Path) -> None:
        steps = (
            _checkout()
            + _pull(tag=_TAG)
            + '      - run: docker run --rm --privileged "${BINFMT}" --install arm64\n'
            "        env:\n"
            f"          BINFMT: {_TAG}\n"
        )
        assert _scan(tmp_path, _job(steps, minutes=15)) == []

    def test_env_on_a_uses_step_does_not_count_as_consumption(
        self, tmp_path: Path
    ) -> None:
        # env on a `uses:` step feeds the action's process, not a shell that
        # could resolve the tag, so it must not vouch for the ladder.
        steps = (
            _checkout() + _pull(tag=_TAG) + "      - uses: some/action@abc # v1\n"
            "        env:\n"
            f"          BINFMT: {_TAG}\n"
        )
        violations = _scan(tmp_path, _job(steps, minutes=15))
        assert len(violations) == 1
        assert _TAG_ORPHANED in violations[0]

    def test_a_tag_nothing_consumes_is_flagged(self, tmp_path: Path) -> None:
        steps = _checkout() + _pull(tag="synthorg-orphan:never-used")
        violations = _scan(tmp_path, _job(steps, minutes=15))
        assert len(violations) == 1
        assert _TAG_ORPHANED in violations[0]

    def test_a_ladder_without_a_local_tag_is_unaffected(self, tmp_path: Path) -> None:
        assert _scan(tmp_path, _job(_checkout() + _pull(), minutes=15)) == []

    def test_a_longer_tag_does_not_vouch_for_a_shorter_one(
        self, tmp_path: Path
    ) -> None:
        # Substring matching would let the version-bumped tag mark the older
        # one consumed, masking a real orphan and inventing a violation
        # against the correct wiring of the longer one.
        longer = f"{_TAG}0"
        steps = (
            _checkout()
            + _pull(tag=_TAG)
            + _pull(tag=longer)
            + f"      - run: docker run --rm --privileged {longer} --install arm64\n"
        )
        violations = _scan(tmp_path, _job(steps, minutes=15))
        assert len(violations) == 1
        assert _TAG_ORPHANED in violations[0]
        assert _TAG in violations[0]

    def test_two_producers_of_one_tag_do_not_self_flag(self, tmp_path: Path) -> None:
        # A producer is never a consumer: judging it as one would accuse the
        # ladder of resolving its own output against a registry.
        steps = (
            _checkout()
            + _pull(tag=_TAG)
            + _pull(tag=_TAG)
            + f"      - run: docker run --rm --privileged {_TAG} --install arm64\n"
        )
        assert _scan(tmp_path, _job(steps, minutes=15)) == []

    def test_consumption_before_the_producer_does_not_count(
        self, tmp_path: Path
    ) -> None:
        # The daemon does not hold the image yet at that point, so an earlier
        # reference cannot vouch for the ladder.
        steps = (
            _checkout()
            + f"      - run: docker run --rm --privileged {_TAG} --install arm64\n"
            + _pull(tag=_TAG)
        )
        violations = _scan(tmp_path, _job(steps, minutes=15))
        assert len(violations) == 1
        assert _TAG_ORPHANED in violations[0]

    def test_an_expression_valued_tag_is_flagged(self, tmp_path: Path) -> None:
        # Matching unevaluated text would let an aliased spelling pass, so the
        # gate fails closed rather than guessing.
        steps = _checkout() + _pull(tag="${{ inputs.local-tag }}")
        violations = _scan(tmp_path, _job(steps, minutes=15))
        assert len(violations) == 1
        assert _TAG_EXPRESSION in violations[0]

    def test_a_tag_both_consumed_and_mishandled_reports_only_the_misuse(
        self, tmp_path: Path
    ) -> None:
        steps = (
            _checkout()
            + _pull(tag=_TAG)
            + f"      - run: docker run --rm --privileged {_TAG} --install arm64\n"
            + _qemu(_TAG)
        )
        violations = _scan(tmp_path, _job(steps, minutes=15))
        assert len(violations) == 1
        assert _TAG_PULLED in violations[0]

    def test_tags_are_judged_independently(self, tmp_path: Path) -> None:
        steps = (
            _checkout()
            + _pull(tag=_BUILDKIT_TAG)
            + _buildx(f"image={_BUILDKIT_TAG}")
            + _pull(tag=_TAG)
            + _qemu(_TAG)
        )
        violations = _scan(tmp_path, _job(steps, minutes=15))
        assert len(violations) == 1
        assert _TAG in violations[0]
        assert _BUILDKIT_TAG not in violations[0]

    def test_the_composite_form_is_scanned_too(self, tmp_path: Path) -> None:
        # Every live call site lives in a composite, so the wiring there is
        # the one that actually has to hold.
        steps = (
            "    - uses: ./.github/actions/docker-pull-resilient\n"
            "      with:\n"
            f"        local-tag: {_TAG}\n"
            "    - uses: docker/setup-qemu-action@abc # v4\n"
            "      with:\n"
            f"        image: {_TAG}\n"
        )
        violations = _scan(tmp_path, _composite(steps))
        assert len(violations) == 1
        assert _TAG_PULLED in violations[0]


class TestDockerfileDigestPins:
    """Every reference BuildKit resolves itself must carry a digest.

    The docker.io mirror on the buildx driver is safe only while that holds:
    a mirror serving different content then fails verification instead of
    being trusted.
    """

    _DIGEST = "@sha256:" + "0" * 64

    def _refs(self, text: str) -> list[tuple[int, str]]:
        refs: list[tuple[int, str]] = _MODULE._dockerfile_refs(text)  # type: ignore[attr-defined]
        return refs

    def test_syntax_from_and_copy_from_are_all_collected(self) -> None:
        text = (
            f"# syntax=docker/dockerfile:1.25{self._DIGEST}\n"
            f"FROM python:3.14-slim{self._DIGEST} AS builder\n"
            f"COPY --from=ghcr.io/astral-sh/uv:0.11{self._DIGEST} /uv /bin/\n"
        )
        assert [ref for _, ref in self._refs(text)] == [
            f"docker/dockerfile:1.25{self._DIGEST}",
            f"python:3.14-slim{self._DIGEST}",
            f"ghcr.io/astral-sh/uv:0.11{self._DIGEST}",
        ]

    def test_a_build_stage_is_not_a_registry_reference(self) -> None:
        # `COPY --from=builder` names a stage in this file, so it can carry no
        # digest and must not be demanded to.
        text = (
            f"FROM python:3.14-slim{self._DIGEST} AS builder\n"
            "COPY --from=builder /app /app\n"
        )
        assert [ref for _, ref in self._refs(text)] == [
            f"python:3.14-slim{self._DIGEST}"
        ]

    @pytest.mark.parametrize(
        "line",
        [
            pytest.param("FROM ${BASE_IMAGE}\n", id="from-braced"),
            pytest.param("FROM $BASE_IMAGE\n", id="from-bare"),
            pytest.param("COPY --from=${STAGE} /app /app\n", id="copy-braced"),
            pytest.param("COPY --from=$STAGE /app /app\n", id="copy-bare"),
        ],
    )
    def test_a_build_arg_reference_is_exempt(self, line: str) -> None:
        # Both spellings are legal Dockerfile syntax and neither resolves
        # against a registry, so neither can carry a digest.
        assert self._refs(line) == []

    def test_a_numeric_copy_from_index_is_exempt(self) -> None:
        assert self._refs("COPY --from=0 /app /app\n") == []

    def test_a_platform_flag_does_not_shadow_the_reference(self) -> None:
        text = f"FROM --platform=linux/amd64 debian:trixie{self._DIGEST}\n"
        assert [ref for _, ref in self._refs(text)] == [f"debian:trixie{self._DIGEST}"]

    def test_a_tag_only_reference_is_reported(self) -> None:
        refs = self._refs("FROM python:3.14-slim AS builder\n")
        assert [ref for _, ref in refs] == ["python:3.14-slim"]
        assert _MODULE._DIGEST_MARKER not in refs[0][1]  # type: ignore[attr-defined]

    def test_the_live_dockerfiles_are_all_pinned(self) -> None:
        # The gate is no-baseline, so the tree has to pass from day one.
        check = _MODULE._check_dockerfile_digest_pins  # type: ignore[attr-defined]
        assert check() == []


class TestScanFileEdgeCases:
    """Error / degenerate inputs are handled, not crashed."""

    def test_yaml_parse_error_returns_violation(self, tmp_path: Path) -> None:
        violations = _scan(tmp_path, "key: [unclosed\n")
        assert len(violations) == 1
        assert "YAML parse error" in violations[0]

    def test_non_mapping_root_returns_empty(self, tmp_path: Path) -> None:
        assert _scan(tmp_path, "- a\n- b\n") == []
        assert _scan(tmp_path, "") == []

    def test_non_dict_job_value_skipped(self, tmp_path: Path) -> None:
        content = (
            f"{_PERMS}jobs:\n  broken: null\n"
            "  ok:\n    runs-on: ubuntu-24.04\n    timeout-minutes: 5\n"
            "    steps: []\n"
        )
        assert _scan(tmp_path, content) == []


class TestRunnerPinned:
    """Invariant 9: no job floats on a rolling runner alias."""

    @staticmethod
    def _one_job(runs_on: str) -> str:
        return (
            f"{_PERMS}jobs:\n  a:\n    runs-on: {runs_on}\n"
            "    timeout-minutes: 5\n    steps: []\n"
        )

    @pytest.mark.parametrize(
        "alias", ["ubuntu-latest", "windows-latest", "macos-latest"]
    )
    def test_rolling_alias_is_flagged(self, tmp_path: Path, alias: str) -> None:
        violations = _scan(tmp_path, self._one_job(alias))
        assert len(violations) == 1
        assert alias in violations[0]

    def test_pinned_image_is_clean(self, tmp_path: Path) -> None:
        assert _scan(tmp_path, self._one_job("ubuntu-24.04")) == []

    def test_pinned_arm_image_is_clean(self, tmp_path: Path) -> None:
        assert _scan(tmp_path, self._one_job("ubuntu-24.04-arm")) == []

    def test_runs_on_expression_is_not_flagged(self, tmp_path: Path) -> None:
        # The per-arch matrix picks its runner with a ternary. It names no
        # rolling alias, and a regex-based Renovate manager cannot see it
        # either, which is part of why this is a gate rather than a manager.
        expression = (
            "${{ matrix.arch == 'arm64' && 'ubuntu-24.04-arm' || 'ubuntu-24.04' }}"
        )
        assert _scan(tmp_path, self._one_job(expression)) == []

    def test_label_list_is_inspected(self, tmp_path: Path) -> None:
        violations = _scan(tmp_path, self._one_job("[self-hosted, ubuntu-latest]"))
        assert len(violations) == 1

    def test_exempt_job_may_keep_the_alias(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rel = _MODULE._relative_path(tmp_path / "wf.yml")  # type: ignore[attr-defined]
        monkeypatch.setitem(
            _MODULE._ROLLING_RUNNER_EXEMPT,  # type: ignore[attr-defined]
            f"{rel}::a",
            "test exemption",
        )
        assert _scan(tmp_path, self._one_job("ubuntu-latest")) == []

    def test_real_cli_test_matrix_is_the_only_exemption(self) -> None:
        # The exemption exists so the cross-platform matrix can prove the CLI
        # builds on whatever GitHub ships as latest. Anything else creeping in
        # would silently reopen the rolling-runner gap.
        assert set(_MODULE._ROLLING_RUNNER_EXEMPT) == {  # type: ignore[attr-defined]
            ".github/workflows/verify-cli.yml::cli-test"
        }


class TestTopLevelPermissions:
    """Invariant 11: no workflow inherits the repository default token scope."""

    _JOB = (
        "jobs:\n  a:\n    runs-on: ubuntu-24.04\n"
        "    timeout-minutes: 5\n    steps: []\n"
    )

    def test_missing_block_flagged(self, tmp_path: Path) -> None:
        violations = _scan(tmp_path, self._JOB)
        assert len(violations) == 1
        assert "permissions" in violations[0]

    def test_empty_block_is_clean(self, tmp_path: Path) -> None:
        # `permissions: {}` parses to a falsy dict, so a truthiness test would
        # reject exactly the value the invariant asks for.
        assert _scan(tmp_path, f"permissions: {{}}\n{self._JOB}") == []

    def test_populated_block_is_clean(self, tmp_path: Path) -> None:
        assert _scan(tmp_path, f"permissions:\n  contents: read\n{self._JOB}") == []


class TestPullRequestTargetRefs:
    """Invariant 12: a pull_request_target job never resolves PR-head content."""

    @staticmethod
    def _wf(trigger: str, steps: str) -> str:
        return (
            f"{trigger}\n{_PERMS}jobs:\n  a:\n    runs-on: ubuntu-24.04\n"
            f"    timeout-minutes: 5\n    steps:\n{steps}"
        )

    _TARGET = "on:\n  pull_request_target:\n    branches: [main]"
    _SAFE = "on:\n  pull_request:\n    branches: [main]"
    _HEAD_CHECKOUT = (
        "      - uses: actions/checkout@abc\n"
        "        with:\n"
        "          ref: ${{ github.event.pull_request.head.sha }}\n"
    )

    def test_head_sha_checkout_flagged(self, tmp_path: Path) -> None:
        violations = _scan(tmp_path, self._wf(self._TARGET, self._HEAD_CHECKOUT))
        assert len(violations) == 1
        assert "pull_request_target" in violations[0]

    def test_head_ref_checkout_flagged(self, tmp_path: Path) -> None:
        steps = (
            "      - uses: actions/checkout@abc\n"
            "        with:\n"
            "          ref: ${{ github.head_ref }}\n"
        )
        assert len(_scan(tmp_path, self._wf(self._TARGET, steps))) == 1

    def test_base_ref_checkout_clean(self, tmp_path: Path) -> None:
        steps = (
            "      - uses: actions/checkout@abc\n        with:\n          ref: main\n"
        )
        assert _scan(tmp_path, self._wf(self._TARGET, steps)) == []

    def test_pr_data_in_run_body_flagged(self, tmp_path: Path) -> None:
        steps = "      - run: echo ${{ github.event.pull_request.head.label }}\n"
        violations = _scan(tmp_path, self._wf(self._TARGET, steps))
        assert len(violations) == 1
        assert "run" in violations[0]

    def test_pr_data_in_step_env_flagged(self, tmp_path: Path) -> None:
        steps = (
            "      - env:\n"
            "          BRANCH: ${{ github.head_ref }}\n"
            "        run: echo hi\n"
        )
        assert len(_scan(tmp_path, self._wf(self._TARGET, steps))) == 1

    def test_pr_data_in_job_env_flagged(self, tmp_path: Path) -> None:
        # A job-level env reaches every `run:` without appearing in one, so a
        # step-only scan would call `echo $BRANCH` compliant.
        content = (
            f"{self._TARGET}\n{_PERMS}jobs:\n  a:\n    runs-on: ubuntu-24.04\n"
            "    timeout-minutes: 5\n"
            "    env:\n      BRANCH: ${{ github.head_ref }}\n"
            "    steps:\n      - run: echo $BRANCH\n"
        )
        violations = _scan(tmp_path, content)
        assert len(violations) == 1
        assert "job 'a' `env:`" in violations[0]

    def test_pr_data_in_workflow_env_flagged(self, tmp_path: Path) -> None:
        content = (
            f"{self._TARGET}\n{_PERMS}env:\n"
            "  REF: ${{ github.event.pull_request.head.sha }}\n"
            "jobs:\n  a:\n    runs-on: ubuntu-24.04\n"
            "    timeout-minutes: 5\n    steps:\n      - run: echo $REF\n"
        )
        violations = _scan(tmp_path, content)
        assert len(violations) == 1
        assert "workflow `env:`" in violations[0]

    def test_base_side_env_is_clean(self, tmp_path: Path) -> None:
        content = (
            f"{self._TARGET}\n{_PERMS}env:\n  REF: ${{{{ github.sha }}}}\n"
            "jobs:\n  a:\n    runs-on: ubuntu-24.04\n"
            "    timeout-minutes: 5\n"
            "    env:\n      BASE: ${{{{ github.event.pull_request.base.sha }}}}\n"
            "    steps:\n      - run: echo $REF $BASE\n"
        )
        assert _scan(tmp_path, content) == []

    def test_same_shape_clean_without_the_trigger(self, tmp_path: Path) -> None:
        # Under plain pull_request the head IS the thing under test, and the
        # job holds no base-repository secrets, so none of this is a finding.
        assert _scan(tmp_path, self._wf(self._SAFE, self._HEAD_CHECKOUT)) == []

    def test_list_form_trigger_detected(self, tmp_path: Path) -> None:
        trigger = "on: [push, pull_request_target]"
        assert len(_scan(tmp_path, self._wf(trigger, self._HEAD_CHECKOUT))) == 1

    def test_quoted_on_key_detected(self, tmp_path: Path) -> None:
        # Bare `on:` parses to the boolean True under YAML 1.1; the quoted
        # spelling must reach the same check.
        trigger = '"on":\n  pull_request_target:\n    branches: [main]'
        assert len(_scan(tmp_path, self._wf(trigger, self._HEAD_CHECKOUT))) == 1


def _worker(name: str) -> str:
    """A plain watched job named ``name``."""
    return (
        f"  {name}:\n"
        "    runs-on: ubuntu-24.04\n"
        "    timeout-minutes: 15\n"
        "    steps:\n"
        "      - run: true\n"
    )


def _scheduled(
    notifiers: str, *, on_key: str = "on", extra_job: str | None = None
) -> str:
    """Build a scheduled workflow with watched job(s) plus ``notifiers``.

    ``on_key`` exists so the YAML-1.1 boolean-key path can be exercised
    against the quoted spelling as well as the bare one. ``extra_job`` adds a
    second watched job, so per-job coverage can be told apart from a union
    across every notifier in the file.
    """
    jobs = _worker("worker") + (_worker(extra_job) if extra_job else "")
    return (
        f"{on_key}:\n  schedule:\n    - cron: 0 7 * * 1\n"
        f"{_PERMS}jobs:\n{jobs}{notifiers}"
    )


def _notifier(
    name: str,
    condition: str,
    *,
    needs: str = "worker",
    uses: str = "./.github/actions/post-tracking-issue",
) -> str:
    """A job reaching the tracking-issue sink under ``condition``."""
    return (
        f"  {name}:\n"
        f"    needs: {needs}\n"
        f"    if: {condition}\n"
        "    runs-on: ubuntu-24.04\n"
        "    timeout-minutes: 5\n"
        "    steps:\n"
        "      - uses: actions/checkout@abc\n"
        f"      - uses: {uses}\n"
    )


def _cond(*clauses: str) -> str:
    """Join result clauses into an ``always()``-guarded condition."""
    return " && ".join(("always()", *clauses))


_FAILURE_ONLY = _cond("needs.worker.result == 'failure'")
_STALL_ONLY = _cond(
    "needs.worker.result != 'success'",
    "needs.worker.result != 'failure'",
    "needs.worker.result != 'skipped'",
)
_BOTH = _cond("needs.worker.result != 'success'", "needs.worker.result != 'skipped'")

_NO_SINK = "has no .github/actions/post-tracking-issue job"
_SAME_SINK = "routes failure and non-completion to the same sink"
_NAMES_NO_JOB = "no notifier condition names a `needs.<job>.result`"


class TestAdmits:
    """The pure predicate behind invariant 10, asserted as exact tuples.

    Driven directly rather than through the composed English message, so a
    reworded violation cannot quietly turn these into vacuous substring
    checks.
    """

    @pytest.mark.parametrize(
        ("equals", "not_equals", "expected"),
        [
            ({"failure"}, set(), (True, False)),
            ({"cancelled"}, set(), (False, True)),
            (set(), {"success", "skipped"}, (True, True)),
            (set(), {"success"}, (True, True)),
            (set(), {"success", "failure", "skipped"}, (False, True)),
            (set(), {"success", "cancelled"}, (True, False)),
            (set(), set(), (False, False)),
            ({"success"}, set(), (False, False)),
        ],
    )
    def test_admission(
        self, equals: set[str], not_equals: set[str], expected: tuple[bool, bool]
    ) -> None:
        admission = _MODULE._admits(equals, not_equals)  # type: ignore[attr-defined]
        assert (admission.failure, admission.stall) == expected


class TestResultComparisons:
    """Comparisons are bound to the job each one names."""

    def test_two_jobs_do_not_pool_their_literals(self) -> None:
        # The bug this prevents: an unbound scan would union both jobs'
        # literals and report full coverage for each.
        condition = _cond(
            "needs.alpha.result == 'failure'", "needs.beta.result == 'cancelled'"
        )
        grouped = _MODULE._result_comparisons(condition)  # type: ignore[attr-defined]
        assert set(grouped) == {"alpha", "beta"}
        assert grouped["alpha"] == ({"failure"}, set())
        assert grouped["beta"] == ({"cancelled"}, set())

    def test_double_quoted_literals_are_not_recognised(self) -> None:
        # GitHub expressions require single quotes, so a double-quoted
        # comparison is invalid config. Reading nothing makes it fail closed.
        assert (
            _MODULE._result_comparisons(  # type: ignore[attr-defined]
                'always() && needs.worker.result == "failure"'
            )
            == {}
        )


class TestScheduleNotifiers:
    """Invariant 10: failure and non-completion reach two different sinks."""

    @pytest.mark.parametrize(
        ("condition", "present", "absent"),
        [
            (_FAILURE_ONLY, "non-completion", "failure"),
            (_STALL_ONLY, "failure", "non-completion"),
        ],
    )
    def test_one_sided_notifier_flagged(
        self, tmp_path: Path, condition: str, present: str, absent: str
    ) -> None:
        # The negative assertion is what makes this precise: without it, a
        # regression that dropped BOTH halves would still contain `present`
        # and pass, which is exactly the case each parameter guards.
        violations = _scan(tmp_path, _scheduled(_notifier("r", condition)))
        assert len(violations) == 1
        assert present in violations[0]
        assert absent not in violations[0]

    def test_one_notifier_covering_both_is_flagged(self, tmp_path: Path) -> None:
        # The C1 shape: a single broad-complement sink catches cancellation
        # under whatever title it carries, so a stalled run is filed as the
        # finding that title asserts.
        violations = _scan(tmp_path, _scheduled(_notifier("r", _BOTH)))
        assert len(violations) == 1
        assert _SAME_SINK in violations[0]
        assert "'r'" in violations[0]

    def test_two_jobs_split_the_coverage(self, tmp_path: Path) -> None:
        content = _scheduled(
            _notifier("report_failure", _FAILURE_ONLY)
            + _notifier("report_stalled", _STALL_ONLY)
        )
        assert _scan(tmp_path, content) == []

    def test_a_broad_sink_paired_with_a_stall_sink_passes(self, tmp_path: Path) -> None:
        # Two distinct sinks exist, so a distinct pair is selectable even
        # though the first also admits cancellation.
        content = _scheduled(
            _notifier("broad", _BOTH) + _notifier("report_stalled", _STALL_ONLY)
        )
        assert _scan(tmp_path, content) == []

    def test_or_joined_cross_job_condition(self, tmp_path: Path) -> None:
        # The maint-ghcr.yml shape, pinned here rather than relying on the
        # live-tree self-check, which would vanish if that file changed.
        content = _scheduled(
            _notifier(
                "report_failure",
                _cond(
                    "( needs.worker.result == 'failure'"
                    " || needs.second.result == 'failure' )"
                ),
                needs="[worker, second]",
            )
            + _notifier(
                "report_stalled",
                _cond(
                    "( needs.worker.result == 'cancelled'"
                    " || needs.second.result == 'cancelled' )"
                ),
                needs="[worker, second]",
            ),
            extra_job="second",
        )
        assert _scan(tmp_path, content) == []

    def test_a_second_watched_job_is_judged_independently(self, tmp_path: Path) -> None:
        # Union-across-notifiers alone would pass this: one sink covers
        # worker's failure, another covers second's stall, and every label
        # appears somewhere. Per-job binding is what catches the two
        # outcomes that reach nobody.
        content = _scheduled(
            _notifier("report_failure", _FAILURE_ONLY)
            + _notifier(
                "report_stalled",
                _cond("needs.second.result == 'cancelled'"),
                needs="second",
            ),
            extra_job="second",
        )
        violations = _scan(tmp_path, content)
        assert len(violations) == 2
        assert any("'worker'" in v and "non-completion" in v for v in violations)
        assert any("'second'" in v and "failure" in v for v in violations)

    def test_no_notifier_at_all_flagged(self, tmp_path: Path) -> None:
        violations = _scan(tmp_path, _scheduled(""))
        assert len(violations) == 1
        assert _NO_SINK in violations[0]

    def test_unscheduled_workflow_needs_no_notifier(self, tmp_path: Path) -> None:
        content = (
            f"on:\n  workflow_dispatch:\n{_PERMS}jobs:\n  worker:\n"
            "    runs-on: ubuntu-24.04\n    timeout-minutes: 5\n"
            "    steps:\n      - run: true\n"
        )
        assert _scan(tmp_path, content) == []

    def test_bare_on_key_is_read_as_a_trigger(self, tmp_path: Path) -> None:
        # PyYAML parses YAML 1.1, so a bare `on:` key arrives as the boolean
        # True. Reading only data["on"] would match no real workflow and the
        # invariant would pass everything forever while looking correct.
        assert True in yaml.safe_load(_scheduled(""))
        assert len(_scan(tmp_path, _scheduled(""))) == 1

    def test_quoted_on_key_is_read_as_a_trigger(self, tmp_path: Path) -> None:
        assert len(_scan(tmp_path, _scheduled("", on_key='"on"'))) == 1

    def test_list_form_on_is_not_a_schedule_trigger(self, tmp_path: Path) -> None:
        # `schedule` carries its cron entries, so it cannot appear in the
        # list form. A gate that accepted it there would carry a branch no
        # input can reach.
        content = (
            f"on: [push, workflow_dispatch]\n{_PERMS}jobs:\n  worker:\n"
            "    runs-on: ubuntu-24.04\n    timeout-minutes: 5\n"
            "    steps:\n      - run: true\n"
        )
        assert _scan(tmp_path, content) == []

    def test_unrecognised_expression_fails_closed(self, tmp_path: Path) -> None:
        # `failure()` admits failure in GitHub's semantics, but this gate
        # models explicit result comparisons only. Crediting an expression
        # it cannot read would hand out coverage on trust.
        violations = _scan(tmp_path, _scheduled(_notifier("r", "failure()")))
        assert len(violations) == 1
        assert _NAMES_NO_JOB in violations[0]

    def test_a_non_notifier_job_does_not_count(self, tmp_path: Path) -> None:
        content = _scheduled(
            "  bystander:\n"
            "    needs: worker\n"
            f"    if: {_BOTH}\n"
            "    runs-on: ubuntu-24.04\n"
            "    timeout-minutes: 5\n"
            "    steps:\n"
            "      - run: echo noted\n"
        )
        violations = _scan(tmp_path, content)
        assert len(violations) == 1
        assert _NO_SINK in violations[0]

    def test_a_neighbour_of_the_sink_is_not_a_sink(self, tmp_path: Path) -> None:
        # Substring matching would credit this and silently under-enforce.
        content = _scheduled(
            _notifier(
                "r", _FAILURE_ONLY, uses="./.github/actions/post-tracking-issue-v2"
            )
        )
        violations = _scan(tmp_path, content)
        assert len(violations) == 1
        assert _NO_SINK in violations[0]

    def test_a_fork_qualified_sink_reference_counts(self, tmp_path: Path) -> None:
        # The other resolved spelling of an in-repo action, which a job must
        # use before its workspace exists. Only the local form appears in the
        # fixtures above, so without this the marker-matching branch could
        # regress and stop recognising real sinks while the suite stayed green.
        content = _scheduled(
            _notifier(
                "report_failure",
                _FAILURE_ONLY,
                uses="Aureliolo/synthorg/.github/actions/post-tracking-issue@abc",
            )
            + _notifier("report_stalled", _STALL_ONLY)
        )
        assert _scan(tmp_path, content) == []

    def test_filing_through_a_wrapper_composite_counts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A sink reached through a local composite is still a sink; without
        # the fixpoint closure the wrapper would hide it and the gate would
        # report a correctly-routed workflow as routing nothing.
        actions = tmp_path / ".github" / "actions"
        (actions / "post-tracking-issue").mkdir(parents=True)
        wrapper = actions / "wrap-issue"
        wrapper.mkdir()
        (wrapper / "action.yml").write_text(
            _composite(
                "    - uses: ./.github/actions/post-tracking-issue\n",
                token_input=False,
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(_MODULE, "_REPO_ROOT", tmp_path)
        monkeypatch.setattr(_MODULE, "_ACTIONS_ROOT", actions)
        content = _scheduled(
            _notifier("report_failure", _FAILURE_ONLY)
            + _notifier(
                "report_stalled", _STALL_ONLY, uses="./.github/actions/wrap-issue"
            )
        )
        assert _scan(tmp_path, content, fresh=True) == []


class TestMain:
    """CLI entry-point routing and self-check."""

    def test_scan_all_clean(self) -> None:
        # No-baseline invariant: the live workflow tree passes the gate.
        rc = _MODULE.main(["--scan-all"])  # type: ignore[attr-defined]
        assert rc == 0

    def test_explicit_path_mode(self, tmp_path: Path) -> None:
        clean = tmp_path / "clean.yml"
        clean.write_text(_job("      - run: true\n"), encoding="utf-8")
        assert _MODULE.main([str(clean)]) == 0  # type: ignore[attr-defined]
        bad = tmp_path / "bad.yml"
        bad.write_text(_job("      - run: true\n", timeout=False), encoding="utf-8")
        assert _MODULE.main([str(bad)]) == 1  # type: ignore[attr-defined]

    def test_disjointness_self_check_exit_2(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An excluded action that is also matched as enforced is a setup
        # error (exit 2), guarding against a future both-sets edit.
        enforced = next(iter(_MODULE._ENFORCED_ACTIONS))  # type: ignore[attr-defined]
        monkeypatch.setattr(
            _MODULE, "_EXCLUDED", {enforced: "conflicting"}, raising=True
        )
        assert _MODULE.main(["--scan-all"]) == 2  # type: ignore[attr-defined]
