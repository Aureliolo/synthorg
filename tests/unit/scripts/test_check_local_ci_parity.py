"""Unit tests for ``scripts/check_local_ci_parity.py``."""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_local_ci_parity.py"


def _load_gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_check_local_ci_parity",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_GATE: Any = cast("Any", _load_gate())  # type: ignore[explicit-any]  # dynamically loaded gate module; attrs resolved by name


# ── _effective_stages ───────────────────────────────────────────


def test_effective_stages_empty_defaults_to_installed() -> None:
    assert _GATE._effective_stages({}) == _GATE._DEFAULT_INSTALLED_STAGES


def test_effective_stages_explicit_list() -> None:
    assert _GATE._effective_stages({"stages": ["pre-push"]}) == frozenset({"pre-push"})


def test_effective_stages_scalar() -> None:
    assert _GATE._effective_stages({"stages": "pre-commit"}) == frozenset(
        {"pre-commit"}
    )


# ── _parse_skip ─────────────────────────────────────────────────


def test_parse_skip_splits_and_strips() -> None:
    assert _GATE._parse_skip("a, b ,c") == frozenset({"a", "b", "c"})


def test_parse_skip_empty_and_none() -> None:
    assert _GATE._parse_skip("") == frozenset()
    assert _GATE._parse_skip(None) == frozenset()


# ── _local_hook_ids ─────────────────────────────────────────────


def test_local_hook_ids_keeps_only_parity_stage_hooks() -> None:
    config = {
        "repos": [
            {
                "hooks": [
                    {"id": "push-gate", "stages": ["pre-push"]},
                    {"id": "both-gate", "stages": ["pre-commit", "pre-push"]},
                    {"id": "msg-only", "stages": ["commit-msg"]},
                    {"id": "default-gate"},  # no stages -> all installed
                ]
            }
        ]
    }
    ids = _GATE._local_hook_ids(config)
    assert "push-gate" in ids
    assert "both-gate" in ids
    assert "default-gate" in ids  # runs at pre-commit + pre-push
    assert "msg-only" not in ids  # commit-msg is not a parity stage


# ── _all_files_invocations ──────────────────────────────────────


def _gates_ci(skip: str, *, both_stages: bool = True) -> dict[str, object]:
    steps: list[dict[str, object]] = [
        {"run": "uv run pre-commit run --all-files --hook-stage pre-commit"},
    ]
    if both_stages:
        steps.append({"run": "uv run pre-commit run --all-files --hook-stage pre-push"})
    return {"jobs": {"gates": {"env": {"SKIP": skip}, "steps": steps}}}


def test_all_files_invocations_parses_stage_and_job_skip() -> None:
    invocations = _GATE._all_files_invocations(_gates_ci("mypy,go-test"))
    stages = {stage for stage, _ in invocations}
    assert stages == {"pre-commit", "pre-push"}
    for _, skip in invocations:
        assert skip == frozenset({"mypy", "go-test"})


def test_all_files_invocations_step_skip_overrides_job_skip() -> None:
    ci = {
        "jobs": {
            "gates": {
                "env": {"SKIP": "job-level"},
                "steps": [
                    {
                        "run": "uv run pre-commit run --all-files",
                        "env": {"SKIP": "step-level"},
                    }
                ],
            }
        }
    }
    invocations = _GATE._all_files_invocations(ci)
    assert invocations == [("pre-commit", frozenset({"step-level"}))]


# ── _parity_violations (maps monkeypatched to isolate the logic) ─


def _write_config(root: Path, hook_id: str) -> None:
    (root / ".pre-commit-config.yaml").write_text(
        "default_install_hook_types: [pre-commit, commit-msg, pre-push]\n"
        "repos:\n"
        "  - repo: local\n"
        "    hooks:\n"
        f"      - id: {hook_id}\n"
        f"        name: {hook_id}\n"
        "        entry: echo\n"
        "        language: system\n"
        "        stages: [pre-push]\n",
        encoding="utf-8",
    )


def _write_ci(root: Path, skip: str, *, both_stages: bool = True) -> None:
    wf_dir = root / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    steps = [
        "      - name: pre-commit stage\n"
        "        run: uv run pre-commit run --all-files --hook-stage pre-commit\n"
    ]
    if both_stages:
        steps.append(
            "      - name: pre-push stage\n"
            "        run: uv run pre-commit run --all-files --hook-stage pre-push\n"
        )
    (wf_dir / "ci.yml").write_text(
        "jobs:\n"
        "  gates:\n"
        "    env:\n"
        f'      SKIP: "{skip}"\n'
        "    steps:\n" + "".join(steps),
        encoding="utf-8",
    )


def test_parity_clean_when_hook_runs_in_all_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(_GATE, "_COVERED_ELSEWHERE", {})
    monkeypatch.setattr(_GATE, "_LOCAL_ONLY", {})
    _write_config(tmp_path, "g1")
    _write_ci(tmp_path, skip="")
    assert _GATE._parity_violations(tmp_path) == []


def test_parity_flags_skipped_hook_with_no_coverage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(_GATE, "_COVERED_ELSEWHERE", {})
    monkeypatch.setattr(_GATE, "_LOCAL_ONLY", {})
    _write_config(tmp_path, "g1")
    _write_ci(tmp_path, skip="g1")
    problems = _GATE._parity_violations(tmp_path)
    assert any("g1" in p and "NO CI counterpart" in p for p in problems)


def test_parity_flags_missing_pre_push_stage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(_GATE, "_COVERED_ELSEWHERE", {})
    monkeypatch.setattr(_GATE, "_LOCAL_ONLY", {})
    _write_config(tmp_path, "g1")
    _write_ci(tmp_path, skip="", both_stages=False)
    problems = _GATE._parity_violations(tmp_path)
    assert any("pre-push" in p for p in problems)


# ── _cardinal_rule_violations ───────────────────────────────────


def _write_cardinal_workflows(root: Path, ci_job_if: str) -> None:
    wf_dir = root / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / "ci.yml").write_text(
        "jobs:\n  my-correctness-job:\n    if: " + ci_job_if + "\n",
        encoding="utf-8",
    )
    # cli.yml must exist (the gate iterates both); keep it clean.
    (wf_dir / "cli.yml").write_text("jobs: {}\n", encoding="utf-8")


def test_cardinal_flags_correctness_job_on_changed_file(tmp_path: Path) -> None:
    _write_cardinal_workflows(tmp_path, "needs.changes.outputs.python == 'true'")
    problems = _GATE._cardinal_rule_violations(tmp_path)
    assert any("my-correctness-job" in p and "python" in p for p in problems)


def test_cardinal_clean_when_only_event_guard(tmp_path: Path) -> None:
    _write_cardinal_workflows(
        tmp_path, "needs.changes.outputs.is_release_please != 'true'"
    )
    assert _GATE._cardinal_rule_violations(tmp_path) == []


# ── live-repo regression ────────────────────────────────────────


def test_real_repo_is_in_parity() -> None:
    # The committed config + workflows must always satisfy both invariants;
    # this is the regression guard that the keystone gate stays green.
    assert _GATE.main(["--repo-root", str(_REPO_ROOT)]) == 0
