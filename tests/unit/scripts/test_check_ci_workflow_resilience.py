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


_LADDER = (
    "      - uses: codecov/codecov-action@abc # v7\n"
    "        id: cov-1\n"
    "        continue-on-error: true\n"
    "      - uses: codecov/codecov-action@abc # v7\n"
    "        id: cov-2\n"
    "        continue-on-error: true\n"
    "      - uses: codecov/codecov-action@abc # v7\n"
)


class TestTimeout:
    """Every non-reusable job must declare timeout-minutes."""

    def test_missing_timeout_flagged(self, tmp_path: Path) -> None:
        content = (
            "jobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: true\n"
        )
        violations = _scan(tmp_path, content)
        assert any("no timeout-minutes" in v for v in violations)

    def test_present_timeout_clean(self, tmp_path: Path) -> None:
        content = (
            "jobs:\n  a:\n    runs-on: ubuntu-latest\n"
            "    timeout-minutes: 10\n    steps:\n      - run: true\n"
        )
        assert _scan(tmp_path, content) == []

    def test_reusable_call_job_exempt(self, tmp_path: Path) -> None:
        # A job whose body is a top-level ``uses:`` cannot set
        # timeout-minutes; it must not be flagged.
        content = "jobs:\n  a:\n    uses: ./.github/workflows/other.yml\n"
        assert _scan(tmp_path, content) == []


class TestLadder:
    """Enforced upload/OIDC actions must be in a fail-closed ladder."""

    def test_bare_single_step_flagged(self, tmp_path: Path) -> None:
        content = (
            "jobs:\n  a:\n    runs-on: ubuntu-latest\n    timeout-minutes: 5\n"
            "    steps:\n"
            "      - uses: codecov/codecov-action@abc # v7\n"
        )
        violations = _scan(tmp_path, content)
        assert any("fail-closed retry ladder" in v for v in violations)

    def test_two_bare_steps_flagged(self, tmp_path: Path) -> None:
        # The original ci.yml shape: two un-laddered codecov steps. Neither
        # carries continue-on-error, so a count-only check would false-pass;
        # the structural check must still flag it.
        content = (
            "jobs:\n  a:\n    runs-on: ubuntu-latest\n    timeout-minutes: 5\n"
            "    steps:\n"
            "      - uses: codecov/codecov-action@abc # v7\n"
            "      - uses: codecov/codecov-action@abc # v7\n"
        )
        violations = _scan(tmp_path, content)
        assert any("fail-closed retry ladder" in v for v in violations)

    def test_proper_ladder_clean(self, tmp_path: Path) -> None:
        content = (
            "jobs:\n  a:\n    runs-on: ubuntu-latest\n    timeout-minutes: 5\n"
            "    steps:\n" + _LADDER
        )
        assert _scan(tmp_path, content) == []

    def test_all_soft_failed_ladder_flagged(self, tmp_path: Path) -> None:
        # Every attempt has continue-on-error -> no fail-closed final
        # attempt -> still a violation (terminal soft-fail is rejected).
        content = (
            "jobs:\n  a:\n    runs-on: ubuntu-latest\n    timeout-minutes: 5\n"
            "    steps:\n"
            "      - uses: codecov/codecov-action@abc # v7\n"
            "        continue-on-error: true\n"
            "      - uses: codecov/codecov-action@abc # v7\n"
            "        continue-on-error: true\n"
        )
        violations = _scan(tmp_path, content)
        assert any("fail-closed retry ladder" in v for v in violations)

    def test_codspeed_bare_flagged(self, tmp_path: Path) -> None:
        content = (
            "jobs:\n  a:\n    runs-on: ubuntu-latest\n    timeout-minutes: 5\n"
            "    steps:\n"
            "      - uses: CodSpeedHQ/action@abc # v4\n"
        )
        violations = _scan(tmp_path, content)
        assert any("fail-closed retry ladder" in v for v in violations)

    def test_excluded_action_ignored(self, tmp_path: Path) -> None:
        # deploy-pages is deliberately excluded; a bare step must NOT flag.
        content = (
            "jobs:\n  a:\n    runs-on: ubuntu-latest\n    timeout-minutes: 5\n"
            "    steps:\n"
            "      - uses: actions/deploy-pages@abc # v5\n"
        )
        assert _scan(tmp_path, content) == []

    def test_unrelated_action_ignored(self, tmp_path: Path) -> None:
        content = (
            "jobs:\n  a:\n    runs-on: ubuntu-latest\n    timeout-minutes: 5\n"
            "    steps:\n"
            "      - uses: actions/checkout@abc # v6\n"
        )
        assert _scan(tmp_path, content) == []


class TestRepoIsClean:
    """The live workflow tree must pass the gate (no-baseline invariant)."""

    def test_scan_all_clean(self) -> None:
        rc = _MODULE.main(["--scan-all"])  # type: ignore[attr-defined]
        assert rc == 0
