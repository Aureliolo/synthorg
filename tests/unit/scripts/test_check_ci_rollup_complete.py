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
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_ci_rollup_complete.py"


def _load_script_module() -> ModuleType:
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
        "verify-backend.yml",
        _rollup_workflow(needs=["alpha", "beta"], results=["alpha", "beta"]),
    )
    problems = _MODULE._rollup_problems("verify-backend.yml", ("ci-pass",), tmp_path)
    assert problems == []


def test_job_missing_from_needs_is_flagged(tmp_path: Path) -> None:
    """The motivating failure: a job runs but nothing gates on it."""
    _write_workflow(
        tmp_path,
        "verify-backend.yml",
        _rollup_workflow(needs=["alpha"], results=["alpha"]),
    )
    problems = _MODULE._rollup_problems("verify-backend.yml", ("ci-pass",), tmp_path)
    assert len(problems) == 1
    assert "'beta' is not in ci-pass.needs" in problems[0]


def test_needs_without_results_is_flagged(tmp_path: Path) -> None:
    """Depended upon but never inspected gates nothing."""
    _write_workflow(
        tmp_path,
        "verify-backend.yml",
        _rollup_workflow(needs=["alpha", "beta"], results=["alpha"]),
    )
    problems = _MODULE._rollup_problems("verify-backend.yml", ("ci-pass",), tmp_path)
    assert len(problems) == 1
    assert "result is never read in RESULTS" in problems[0]


def test_results_without_needs_is_flagged(tmp_path: Path) -> None:
    """A RESULTS entry with no matching need expands to an empty string."""
    _write_workflow(
        tmp_path,
        "verify-backend.yml",
        _rollup_workflow(needs=["alpha"], results=["alpha", "beta"]),
    )
    problems = _MODULE._rollup_problems("verify-backend.yml", ("ci-pass",), tmp_path)
    # `beta` is both absent from needs and unreferenced there, so it trips
    # the coverage check and the RESULTS-without-needs check.
    assert any("not in ci-pass.needs, so the expression" in p for p in problems)


def test_exempt_job_is_not_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exempted job may sit outside the rollup."""
    monkeypatch.setitem(_MODULE._EXEMPT, "verify-backend.yml::beta", "test exemption")
    _write_workflow(
        tmp_path,
        "verify-backend.yml",
        _rollup_workflow(needs=["alpha"], results=["alpha"]),
    )
    problems = _MODULE._rollup_problems("verify-backend.yml", ("ci-pass",), tmp_path)
    assert problems == []


def test_missing_rollup_job_is_flagged(tmp_path: Path) -> None:
    """A renamed or deleted rollup is reported, not silently skipped."""
    _write_workflow(
        tmp_path,
        "verify-backend.yml",
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
    problems = _MODULE._rollup_problems("verify-backend.yml", ("ci-pass",), tmp_path)
    assert len(problems) == 1
    assert "rollup job 'ci-pass' not found" in problems[0]


def test_required_context_without_producer_is_flagged(tmp_path: Path) -> None:
    """A required context nothing emits blocks every PR permanently."""
    _write_workflow(
        tmp_path,
        "verify-backend.yml",
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
        "verify-backend.yml",
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


def test_job_id_is_a_context_when_name_is_absent(tmp_path: Path) -> None:
    """GitHub reports the job id when a job declares no ``name:``."""
    _write_workflow(
        tmp_path,
        "verify-backend.yml",
        """
name: Sample
on: [push]
jobs:
  unnamed-rollup:
    runs-on: ubuntu-24.04
    steps:
      - run: "true"
""",
    )
    (tmp_path / ".github" / "branch_protection.yml").write_text(
        """
rulesets:
  - name: protect-main
    rules:
      - type: required_status_checks
        parameters:
          required_status_checks:
            - context: "unnamed-rollup"
""",
        encoding="utf-8",
    )
    assert _MODULE._context_problems(tmp_path) == []


def test_missing_spec_raises_rather_than_reporting_clean(tmp_path: Path) -> None:
    """An unreadable spec must not read as 'nothing is required'."""
    _write_workflow(
        tmp_path,
        "verify-backend.yml",
        _rollup_workflow(needs=["alpha", "beta"], results=["alpha", "beta"]),
    )
    with pytest.raises(_MODULE.SpecShapeError):
        _MODULE._context_problems(tmp_path)


def test_spec_without_rulesets_raises(tmp_path: Path) -> None:
    """The drift case: the key was renamed, so nothing could be checked."""
    _write_workflow(
        tmp_path,
        "verify-backend.yml",
        _rollup_workflow(needs=["alpha", "beta"], results=["alpha", "beta"]),
    )
    (tmp_path / ".github" / "branch_protection.yml").write_text(
        "policies:\n  - name: protect-main\n", encoding="utf-8"
    )
    with pytest.raises(_MODULE.SpecShapeError):
        _MODULE._context_problems(tmp_path)


def test_main_exits_two_on_unreadable_spec(tmp_path: Path) -> None:
    """A spec-shape failure is an infrastructure error, not a violation."""
    _write_workflow(
        tmp_path,
        "verify-backend.yml",
        _rollup_workflow(needs=["alpha", "beta"], results=["alpha", "beta"]),
    )
    assert _MODULE.main(["--repo-root", str(tmp_path)]) == 2


def test_hyphenated_job_ids_are_parsed(tmp_path: Path) -> None:
    """Real job ids are hyphenated and multi-segment, unlike the fixtures."""
    _write_workflow(
        tmp_path,
        "verify-backend.yml",
        """
name: Sample
on: [push]
jobs:
  test-unit:
    name: Test Unit
    runs-on: ubuntu-24.04
    steps:
      - run: "true"
  type-check-pyright:
    name: Type Check
    runs-on: ubuntu-24.04
    steps:
      - run: "true"
  ci-pass:
    name: CI Pass
    if: always()
    needs: [test-unit, type-check-pyright]
    runs-on: ubuntu-24.04
    steps:
      - name: Check results
        env:
          RESULTS: >-
            ${{ needs.test-unit.result }}
            ${{ needs.type-check-pyright.result }}
        run: "true"
""",
    )
    assert _MODULE._rollup_problems("verify-backend.yml", ("ci-pass",), tmp_path) == []


def test_outputs_reference_is_not_read_as_a_gated_job(tmp_path: Path) -> None:
    """``needs.X.outputs.Y`` alongside RESULTS must not raise a false alarm."""
    _write_workflow(
        tmp_path,
        "verify-backend.yml",
        """
name: Sample
on: [push]
jobs:
  alpha:
    name: Alpha
    runs-on: ubuntu-24.04
    steps:
      - run: "true"
  changes:
    name: Changes
    runs-on: ubuntu-24.04
    steps:
      - run: "true"
  ci-pass:
    name: CI Pass
    if: always()
    needs: [alpha, changes]
    runs-on: ubuntu-24.04
    steps:
      - name: Check results
        env:
          RESULTS: >-
            ${{ needs.alpha.result }}
            ${{ needs.changes.result }}
          WEB_CHANGED: ${{ needs.changes.outputs.web }}
        run: "true"
""",
    )
    assert _MODULE._rollup_problems("verify-backend.yml", ("ci-pass",), tmp_path) == []


def test_an_output_named_result_is_not_read_as_a_gated_job(tmp_path: Path) -> None:
    """``needs.X.outputs.result`` is an output, not a job id.

    A substring test for ``.result`` took everything before the first match,
    yielding the job id ``changes.outputs``. That id is in no ``needs`` list,
    so the gate reported a violation nobody could fix and blocked the PR.
    """
    _write_workflow(
        tmp_path,
        "verify-backend.yml",
        """
name: Sample
on: [push]
jobs:
  alpha:
    name: Alpha
    runs-on: ubuntu-24.04
    steps:
      - run: "true"
  changes:
    name: Changes
    runs-on: ubuntu-24.04
    steps:
      - run: "true"
  ci-pass:
    name: CI Pass
    if: always()
    needs: [alpha, changes]
    runs-on: ubuntu-24.04
    steps:
      - name: Check results
        env:
          RESULTS: >-
            ${{ needs.alpha.result }}
            ${{ needs.changes.result }}
          FILTER_VERDICT: ${{ needs.changes.outputs.result }}
          FILTER_VERDICTS: ${{ needs.changes.outputs.results }}
        run: "true"
""",
    )
    assert _MODULE._rollup_problems("verify-backend.yml", ("ci-pass",), tmp_path) == []


def test_coverage_spans_the_union_of_several_rollups(tmp_path: Path) -> None:
    """A workflow may split its jobs across one rollup per required context."""
    _write_workflow(
        tmp_path,
        "perf.yml",
        """
name: Perf
on: [push]
jobs:
  bench-python:
    name: Bench Python
    runs-on: ubuntu-24.04
    steps:
      - run: "true"
  bench-web:
    name: Bench Web
    runs-on: ubuntu-24.04
    steps:
      - run: "true"
  python-pass:
    name: Python Pass
    if: always()
    needs: [bench-python]
    runs-on: ubuntu-24.04
    steps:
      - name: Check results
        env:
          RESULTS: >-
            ${{ needs.bench-python.result }}
        run: "true"
  web-pass:
    name: Web Pass
    if: always()
    needs: [bench-web]
    runs-on: ubuntu-24.04
    steps:
      - name: Check results
        env:
          RESULTS: >-
            ${{ needs.bench-web.result }}
        run: "true"
""",
    )
    problems = _MODULE._rollup_problems(
        "perf.yml", ("python-pass", "web-pass"), tmp_path
    )
    assert problems == []


def test_job_outside_every_rollup_is_flagged(tmp_path: Path) -> None:
    """A job covered by neither rollup still gates nothing."""
    _write_workflow(
        tmp_path,
        "perf.yml",
        """
name: Perf
on: [push]
jobs:
  bench-python:
    name: Bench Python
    runs-on: ubuntu-24.04
    steps:
      - run: "true"
  orphan:
    name: Orphan
    runs-on: ubuntu-24.04
    steps:
      - run: "true"
  python-pass:
    name: Python Pass
    if: always()
    needs: [bench-python]
    runs-on: ubuntu-24.04
    steps:
      - name: Check results
        env:
          RESULTS: >-
            ${{ needs.bench-python.result }}
        run: "true"
  web-pass:
    name: Web Pass
    if: always()
    needs: [bench-python]
    runs-on: ubuntu-24.04
    steps:
      - name: Check results
        env:
          RESULTS: >-
            ${{ needs.bench-python.result }}
        run: "true"
""",
    )
    problems = _MODULE._rollup_problems(
        "perf.yml", ("python-pass", "web-pass"), tmp_path
    )
    assert len(problems) == 1
    assert "'orphan' is not in python-pass.needs / web-pass.needs" in problems[0]


def test_every_required_context_workflow_is_in_rollups() -> None:
    """A workflow producing a required context must have property 1 enforced.

    Property 3 alone would still pass for a workflow left out of ``_ROLLUPS``,
    while properties 1 and 2 silently stopped applying to it.
    """
    required = _MODULE._required_contexts(_REPO_ROOT)
    workflow_dir = _REPO_ROOT / _MODULE._WORKFLOW_DIR
    for pattern in _MODULE._WORKFLOW_GLOBS:
        for path in sorted(workflow_dir.glob(pattern)):
            jobs = _MODULE._jobs(_MODULE._load(path))
            names = {
                job.get("name") if isinstance(job.get("name"), str) else job_id
                for job_id, job in jobs.items()
            }
            if names & required:
                assert path.name in _MODULE._ROLLUPS, (
                    f"{path.name} produces a required context but is not in "
                    "_ROLLUPS, so a new job there would not have to be wired "
                    "into its rollup."
                )


def test_workflow_discovery_covers_both_yaml_extensions() -> None:
    """GitHub honours ``.yaml`` too, so a one-extension walk measures a subset."""
    assert set(_MODULE._WORKFLOW_GLOBS) == {"*.yml", "*.yaml"}


def test_produced_contexts_reads_a_yaml_extension_workflow(tmp_path: Path) -> None:
    """A ``.yaml`` workflow's job names are contexts like any other."""
    _write_workflow(
        tmp_path,
        "extra.yaml",
        """
name: Extra
on: [push]
jobs:
  solo:
    name: Solo Pass
    runs-on: ubuntu-24.04
    steps:
      - run: "true"
""",
    )
    assert "Solo Pass" in _MODULE._produced_contexts(tmp_path)


# ── top-level paths: filter ─────────────────────────────────────


def _spec_requiring(context: str) -> str:
    """Build a branch-protection spec requiring exactly one context."""
    return f"""
rulesets:
  - name: protect-main
    rules:
      - type: required_status_checks
        parameters:
          required_status_checks:
            - context: "{context}"
"""


def test_top_level_paths_filter_on_a_rollup_workflow_is_flagged(
    tmp_path: Path,
) -> None:
    """A filtered rollup workflow leaves its required context never reported."""
    _write_workflow(
        tmp_path,
        "verify-backend.yml",
        """
name: Sample
on:
  pull_request:
    paths:
      - "src/**"
jobs:
  ci-pass:
    name: CI Pass
    runs-on: ubuntu-24.04
    steps:
      - run: "true"
""",
    )
    (tmp_path / ".github" / "branch_protection.yml").write_text(
        _spec_requiring("CI Pass"), encoding="utf-8"
    )
    problems = _MODULE._context_problems(tmp_path)
    assert len(problems) == 1
    assert "top-level 'paths:' filter" in problems[0]
    assert "pull_request" in problems[0]


def test_top_level_paths_ignore_filter_on_a_rollup_workflow_is_flagged(
    tmp_path: Path,
) -> None:
    """``paths-ignore`` reaches the same deadlock as ``paths``.

    They are the positive and negative spellings of one feature, so policing
    only the positive one leaves the deadlock reachable by inverting the
    filter.
    """
    _write_workflow(
        tmp_path,
        "verify-backend.yml",
        """
name: Sample
on:
  pull_request:
    paths-ignore:
      - "docs/**"
jobs:
  ci-pass:
    name: CI Pass
    runs-on: ubuntu-24.04
    steps:
      - run: "true"
""",
    )
    (tmp_path / ".github" / "branch_protection.yml").write_text(
        _spec_requiring("CI Pass"), encoding="utf-8"
    )
    problems = _MODULE._context_problems(tmp_path)
    assert len(problems) == 1
    assert "top-level 'paths-ignore:' filter" in problems[0]
    assert "pull_request" in problems[0]


def test_unfiltered_rollup_workflow_passes(tmp_path: Path) -> None:
    """The same workflow without the filter is clean."""
    _write_workflow(
        tmp_path,
        "verify-backend.yml",
        """
name: Sample
on:
  pull_request:
    branches: [main]
jobs:
  ci-pass:
    name: CI Pass
    runs-on: ubuntu-24.04
    steps:
      - run: "true"
""",
    )
    (tmp_path / ".github" / "branch_protection.yml").write_text(
        _spec_requiring("CI Pass"), encoding="utf-8"
    )
    assert _MODULE._context_problems(tmp_path) == []


def test_triggers_reads_the_yaml_bool_on_key(tmp_path: Path) -> None:
    """YAML 1.1 turns ``on:`` into ``True``; reading only ``"on"`` sees nothing.

    Without this the paths-filter check would report clean on every real
    workflow file regardless of what it declared.

    Built in a fixture rather than read from a repository workflow: the
    behaviour under test is the YAML loader's key coercion, so pointing at a
    real file would make an unrelated edit to that workflow able to break this
    test, and would let the test pass for the wrong reason if the file stopped
    using the mapping form.
    """
    _write_workflow(
        tmp_path,
        "sample.yml",
        """
name: Sample
on:
  pull_request:
    branches: [main]
jobs:
  solo:
    runs-on: ubuntu-24.04
    steps:
      - run: "true"
""",
    )
    parsed = _MODULE._load(tmp_path / ".github" / "workflows" / "sample.yml")
    assert "on" not in parsed
    assert True in parsed
    assert _MODULE._triggers(parsed) != {}


# ── release-PR status contexts ──────────────────────────────────


def _release_cut_posting(contexts: list[str]) -> str:
    """Build a release-cut.yml whose status step posts the given contexts."""
    listed = " \\\n            ".join(f'"{context}"' for context in contexts)
    return f"""
name: Release - Cut
on: [push]
jobs:
  release-please:
    runs-on: ubuntu-24.04
    steps:
      - name: {_MODULE._STATUS_STEP_NAME}
        run: |
          for context in \\
            {listed}; do
            echo "$context"
          done
"""


def test_release_pr_missing_context_is_flagged(tmp_path: Path) -> None:
    """A required context the release PR never receives wedges that PR."""
    _write_workflow(tmp_path, "release-cut.yml", _release_cut_posting(["CI Pass"]))
    (tmp_path / ".github" / "branch_protection.yml").write_text(
        """
rulesets:
  - name: protect-main
    rules:
      - type: required_status_checks
        parameters:
          required_status_checks:
            - context: "CI Pass"
            - context: "Docker Pass"
""",
        encoding="utf-8",
    )
    problems = _MODULE._release_pr_problems(tmp_path)
    assert len(problems) == 1
    assert "Docker Pass" in problems[0]


def test_release_pr_covering_every_context_passes(tmp_path: Path) -> None:
    """Posting the full required set is clean."""
    _write_workflow(
        tmp_path, "release-cut.yml", _release_cut_posting(["CI Pass", "Docker Pass"])
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
            - context: "Docker Pass"
""",
        encoding="utf-8",
    )
    assert _MODULE._release_pr_problems(tmp_path) == []


def test_release_pr_unreadable_step_is_flagged(tmp_path: Path) -> None:
    """A renamed step must fail loudly, not read as zero missing contexts."""
    _write_workflow(
        tmp_path,
        "release-cut.yml",
        """
name: Release - Cut
on: [push]
jobs:
  release-please:
    runs-on: ubuntu-24.04
    steps:
      - name: Post something else entirely
        run: "true"
""",
    )
    (tmp_path / ".github" / "branch_protection.yml").write_text(
        _spec_requiring("CI Pass"), encoding="utf-8"
    )
    problems = _MODULE._release_pr_problems(tmp_path)
    assert len(problems) == 1
    assert "could not read" in problems[0]


def test_real_tree_passes() -> None:
    """No-baseline claim: the gate is clean against this repository."""
    assert _MODULE.main(["--repo-root", str(_REPO_ROOT)]) == 0
