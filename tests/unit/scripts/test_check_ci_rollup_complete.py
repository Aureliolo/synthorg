"""Unit tests for ``scripts/check_ci_rollup_complete.py``.

Loads the script as a module so its private helpers are callable without
spawning subprocesses.

Covers all three properties the gate exists to hold:

* A job absent from the rollup's ``needs`` is flagged, unless it carries
  an ``_EXEMPT`` entry. This is the failure that motivated the gate: a
  job that runs, goes red, and leaves the required check green.
* ``needs`` and the hand-written ``RESULTS`` env block must name the same
  set, in both directions. A job in ``needs`` but not ``RESULTS`` is
  waited on without being gated on; one in ``RESULTS`` but not ``needs``
  expands to an empty string and gates nothing.
* Every context ``branch_protection.yml`` requires is produced by some
  job's ``name:``, so a rename cannot leave a required context that
  nothing reports (which blocks every PR permanently rather than
  failing loudly).

Also asserts the gate passes against the real tree, so the no-baseline
claim in its docstring stays true.
"""

import importlib.util
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_ci_rollup_complete.py"


def _load_script_module() -> Any:
    """Import the script as a module so private helpers are callable."""
    spec = importlib.util.spec_from_file_location(
        "_check_ci_rollup_complete",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load_script_module()


def _write_workflow(root: Path, name: str, body: str) -> None:
    """Place a workflow file inside a fixture tree."""
    workflows = root / ".github" / "workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    (workflows / name).write_text(body, encoding="utf-8")


def _rollup_workflow(
    *, needs: list[str], results: list[str], extra_jobs: str = ""
) -> str:
    """Build a minimal workflow whose rollup names the given needs/results."""
    needs_yaml = ", ".join(needs)
    results_lines = "\n".join(
        f"            ${{{{ needs.{job}.result }}}}" for job in results
    )
    return f"""
name: Sample
on: [push]
jobs:
  alpha:
    name: Alpha
    runs-on: ubuntu-24.04
    steps:
      - run: "true"
  beta:
    name: Beta
    runs-on: ubuntu-24.04
    steps:
      - run: "true"
{extra_jobs}
  ci-pass:
    name: CI Pass
    if: always()
    needs: [{needs_yaml}]
    runs-on: ubuntu-24.04
    steps:
      - name: Check results
        env:
          RESULTS: >-
{results_lines}
        run: "true"
"""


def test_full_coverage_passes(tmp_path: Path) -> None:
    """A rollup naming every job in both places is clean."""
    _write_workflow(
        tmp_path,
        "ci.yml",
        _rollup_workflow(needs=["alpha", "beta"], results=["alpha", "beta"]),
    )
    problems = _MODULE._rollup_problems("ci.yml", "ci-pass", tmp_path)
    assert problems == []


def test_job_missing_from_needs_is_flagged(tmp_path: Path) -> None:
    """The motivating failure: a job runs but nothing gates on it."""
    _write_workflow(
        tmp_path,
        "ci.yml",
        _rollup_workflow(needs=["alpha"], results=["alpha"]),
    )
    problems = _MODULE._rollup_problems("ci.yml", "ci-pass", tmp_path)
    assert len(problems) == 1
    assert "'beta' is not in ci-pass.needs" in problems[0]


def test_needs_without_results_is_flagged(tmp_path: Path) -> None:
    """Depended upon but never inspected gates nothing."""
    _write_workflow(
        tmp_path,
        "ci.yml",
        _rollup_workflow(needs=["alpha", "beta"], results=["alpha"]),
    )
    problems = _MODULE._rollup_problems("ci.yml", "ci-pass", tmp_path)
    assert len(problems) == 1
    assert "result is never read in RESULTS" in problems[0]


def test_results_without_needs_is_flagged(tmp_path: Path) -> None:
    """A RESULTS entry with no matching need expands to an empty string."""
    _write_workflow(
        tmp_path,
        "ci.yml",
        _rollup_workflow(needs=["alpha"], results=["alpha", "beta"]),
    )
    problems = _MODULE._rollup_problems("ci.yml", "ci-pass", tmp_path)
    # `beta` is both absent from needs and unreferenced there, so it trips
    # the coverage check and the RESULTS-without-needs check.
    assert any("not in ci-pass.needs, so the expression" in p for p in problems)


def test_exempt_job_is_not_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exempted job may sit outside the rollup."""
    monkeypatch.setitem(_MODULE._EXEMPT, "ci.yml::beta", "test exemption")
    _write_workflow(
        tmp_path,
        "ci.yml",
        _rollup_workflow(needs=["alpha"], results=["alpha"]),
    )
    problems = _MODULE._rollup_problems("ci.yml", "ci-pass", tmp_path)
    assert problems == []


def test_missing_rollup_job_is_flagged(tmp_path: Path) -> None:
    """A renamed or deleted rollup is reported, not silently skipped."""
    _write_workflow(
        tmp_path,
        "ci.yml",
        """
name: Sample
on: [push]
jobs:
  alpha:
    name: Alpha
    runs-on: ubuntu-24.04
    steps:
      - run: "true"
""",
    )
    problems = _MODULE._rollup_problems("ci.yml", "ci-pass", tmp_path)
    assert len(problems) == 1
    assert "rollup job 'ci-pass' not found" in problems[0]


def test_required_context_without_producer_is_flagged(tmp_path: Path) -> None:
    """A required context nothing emits blocks every PR permanently."""
    _write_workflow(
        tmp_path,
        "ci.yml",
        _rollup_workflow(needs=["alpha", "beta"], results=["alpha", "beta"]),
    )
    (tmp_path / ".github" / "branch_protection.yml").write_text(
        """
rulesets:
  - name: protect-main
    rules:
      - type: required_status_checks
        parameters:
          required_status_checks:
            - context: "CI Pass"
            - context: "Nonexistent Pass"
""",
        encoding="utf-8",
    )
    problems = _MODULE._context_problems(tmp_path)
    assert len(problems) == 1
    assert "Nonexistent Pass" in problems[0]


def test_required_context_with_producer_passes(tmp_path: Path) -> None:
    """A context some job's name produces is satisfied."""
    _write_workflow(
        tmp_path,
        "ci.yml",
        _rollup_workflow(needs=["alpha", "beta"], results=["alpha", "beta"]),
    )
    (tmp_path / ".github" / "branch_protection.yml").write_text(
        """
rulesets:
  - name: protect-main
    rules:
      - type: required_status_checks
        parameters:
          required_status_checks:
            - context: "CI Pass"
""",
        encoding="utf-8",
    )
    assert _MODULE._context_problems(tmp_path) == []


def test_real_tree_passes() -> None:
    """No-baseline claim: the gate is clean against this repository."""
    assert _MODULE.main(["--repo-root", str(_REPO_ROOT)]) == 0
