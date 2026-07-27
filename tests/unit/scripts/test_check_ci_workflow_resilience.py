"""Unit tests for ``scripts/check_ci_workflow_resilience.py``.

Loads the script as a module so its private helpers are callable without
spawning subprocesses.

Covers all eight invariants:

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
"""

import importlib.util
from pathlib import Path

import pytest

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


def _scan(tmp_path: Path, content: str) -> list[str]:
    """Write content to a tmp .yml file and return the violation messages."""
    target = tmp_path / "wf.yml"
    target.write_text(content, encoding="utf-8")
    violations: list[str] = _MODULE._scan_file(target)  # type: ignore[attr-defined]
    return violations


def _job(
    steps: str,
    *,
    timeout: bool = True,
    minutes: int = 5,
    permissions: str | None = "actions: read",
) -> str:
    """Build a one-job workflow whose ``steps:`` is ``steps``.

    The job grants ``actions: read`` by default so a fixture that happens to
    download an artifact still isolates the invariant under test; pass
    ``permissions=None`` to exercise invariant 5's permission half. ``minutes``
    sets the budget invariant 6 measures a pull ladder against.
    """
    head = "jobs:\n  a:\n    runs-on: ubuntu-latest\n"
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
        content = "jobs:\n  a:\n    uses: ./.github/workflows/other.yml\n"
        assert _scan(tmp_path, content) == []

    def test_null_uses_still_timeout_checked(self, tmp_path: Path) -> None:
        # A malformed bare ``uses:`` (parses to None) is NOT a real
        # reusable-call job, so it must still be timeout-checked.
        content = "jobs:\n  a:\n    uses:\n"
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
        content = "permissions:\n  actions: read\n" + _job(
            _checkout() + _download(), permissions=None
        )
        assert _scan(tmp_path, content) == []

    def test_job_block_overrides_rather_than_merges(self, tmp_path: Path) -> None:
        # GitHub replaces the workflow-level block wholesale, so a job that
        # declares any scope and omits `actions` has genuinely dropped it.
        content = "permissions:\n  actions: read\n" + _job(
            _checkout() + _download(), permissions="contents: read"
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
        return _MODULE._dockerfile_refs(text)  # type: ignore[attr-defined]

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

    def test_a_build_arg_base_is_exempt(self) -> None:
        assert self._refs("FROM ${BASE_IMAGE}\n") == []

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
            "jobs:\n  broken: null\n"
            "  ok:\n    runs-on: ubuntu-latest\n    timeout-minutes: 5\n"
            "    steps: []\n"
        )
        assert _scan(tmp_path, content) == []


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
