"""Unit tests for ``scripts/check_ci_workflow_resilience.py``.

Loads the script as a module so its private helpers are callable without
spawning subprocesses.

Covers both invariants:

* ``timeout-minutes`` required on every job, with the reusable-workflow
  -call exemption (a job whose body is a top-level ``uses:``).
* Enforced external/OIDC upload actions must be in a fail-closed retry
  ladder: a bare single step is flagged, a proper ladder (>=1 attempt
  with ``continue-on-error`` AND >=1 without) passes, and an
  all-soft-failed ladder is flagged. Excluded / unrelated actions are
  ignored.
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


def _job(steps: str, *, timeout: bool = True) -> str:
    """Build a one-job workflow whose ``steps:`` is ``steps``."""
    head = "jobs:\n  a:\n    runs-on: ubuntu-latest\n"
    if timeout:
        head += "    timeout-minutes: 5\n"
    return f"{head}    steps:\n{steps}"


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
        # The original ci.yml shape: two un-laddered codecov steps. Neither
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
        # The finding-1 regression: two independent codecov ladders in one
        # job (separated by a non-codecov step), the first a bare pair. The
        # state machine must flag the bare pair even though the second
        # ladder is well-formed (a naive aggregate check would not).
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
