# module-kind: tests
"""The wire-level smoke: each treatment read off evidence, never off config.

What makes a finding honest is asserted on doubles: what it reads, when it
refuses to say anything, and that a recording cannot start without it.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evals.errors import RecursionDepthSmokeRequiredError
from evals.harness.stall_watch import ProgressTrackingLedger
from evals.recursion_depth.manifest import Arm, load_manifest
from evals.recursion_depth.models import WiringFinding, WiringReport
from evals.recursion_depth.wire_check import (
    WIRING_REPORT_NAME,
    budget_finding,
    caching_finding,
    governance_findings,
    load_wiring_report,
    reasoning_finding,
    require_passing_smoke,
    smoke_dir,
    smoke_manifest,
    stagnation_finding,
    tool_surface_finding,
    write_wiring_report,
)
from synthorg.budget.cost_record import CostRecord
from synthorg.core.types import NotBlankStr
from synthorg.engine.wiring_summary import EngineWiringSummary

pytestmark = pytest.mark.unit

_MANIFEST = (
    Path(__file__).resolve().parents[3] / "evals" / "recursion_depth" / "manifest.yaml"
)


def _wiring(**overrides: object) -> EngineWiringSummary:
    """A fully wired engine's summary, with *overrides* applied.

    Returns:
        The summary.
    """
    base: dict[str, object] = {
        "loop_type": "react",
        "has_tool_registry": True,
        "has_cost_tracker": True,
        "has_budget_enforcer": True,
        "has_coordinator": False,
        "has_compaction_callback": True,
        "has_stagnation_detector": True,
        "stagnation_strategy": "tool_repetition",
        "has_review_pipeline": True,
        "has_memory_backend": True,
        "has_sub_agent_runner": False,
        "has_approval_gate": True,
        "has_policy_engine": True,
        "cost_tracker": None,
        "tool_surface": ("read_file", "shell_command", "write_file"),
    }
    base.update(overrides)
    return EngineWiringSummary(**base)  # type: ignore[arg-type]


def _record(cache_read: int) -> CostRecord:
    """One cost record carrying *cache_read* cached tokens.

    Returns:
        The record.
    """
    return CostRecord(
        agent_id="agent-1",
        task_id="task-1",
        provider="test-provider",
        model="test-model",
        input_tokens=100,
        output_tokens=50,
        cost=0.0,
        currency="EUR",
        timestamp=datetime(2026, 9, 1, tzinfo=UTC),
        cache_read_input_tokens=cache_read,
    )


class TestTheSmokeMatrix:
    """One cell, the shallowest cap, the arm that exercises the reviewer."""

    def test_it_narrows_to_one_gated_cell(self) -> None:
        manifest = load_manifest(_MANIFEST)

        narrowed = smoke_manifest(manifest)

        assert narrowed.depths == (min(manifest.depths),)
        assert narrowed.repetitions == {min(manifest.depths): 1}
        assert narrowed.arms == (Arm.GATED,)

    def test_it_leaves_every_treatment_as_declared(self) -> None:
        manifest = load_manifest(_MANIFEST)

        narrowed = smoke_manifest(manifest)

        assert narrowed.stagnation == manifest.stagnation
        assert narrowed.compaction == manifest.compaction
        assert narrowed.embedder == manifest.embedder


class TestEngineFindings:
    """Read off the engine's own summary, which is what the loop ran with."""

    def test_the_tool_surface_is_named_not_counted(self) -> None:
        finding = tool_surface_finding(_wiring())

        assert finding.passed is True
        assert "shell_command" in finding.observed

    def test_an_engine_that_never_built_an_invoker_fails(self) -> None:
        finding = tool_surface_finding(_wiring(tool_surface=None))

        assert finding.passed is False

    def test_the_declared_detector_must_be_the_one_watching(self) -> None:
        manifest = load_manifest(_MANIFEST)

        other = _wiring(stagnation_strategy="off_by_one")

        assert stagnation_finding(_wiring(), manifest).passed is True
        assert stagnation_finding(other, manifest).passed is False

    def test_the_budget_must_bound_the_cell_ledger_itself(self) -> None:
        ledger = ProgressTrackingLedger()

        same = budget_finding(_wiring(cost_tracker=ledger), ledger)
        other = budget_finding(_wiring(cost_tracker=ProgressTrackingLedger()), ledger)

        assert same.passed is True
        assert other.passed is False
        assert "another tracker" in other.observed

    def test_the_three_governance_seams_are_each_their_own_finding(self) -> None:
        findings = governance_findings(_wiring(has_policy_engine=False))

        verdicts = {finding.treatment: finding.passed for finding in findings}
        assert verdicts == {
            "review pipeline": True,
            "approval gate": True,
            "policy engine": False,
        }


class TestReasoningOffTheWire:
    """The request body is the only place the depth is known to have arrived."""

    def _transcripts(self, tmp_path: Path, lines: list[object]) -> Path:
        root = tmp_path / "transcripts"
        root.mkdir()
        rendered = [
            line if isinstance(line, str) else json.dumps(line) for line in lines
        ]
        (root / "unit-1.jsonl").write_text("\n".join(rendered) + "\n", encoding="utf-8")
        return root

    def test_a_request_carrying_another_depth_fails(self, tmp_path: Path) -> None:
        manifest = load_manifest(_MANIFEST)
        body = {"model": manifest.executor.model_id, "reasoning_effort": "xhigh"}
        root = self._transcripts(
            tmp_path, [{"request": json.dumps(body), "response": "{}"}]
        )

        finding = reasoning_finding(root, manifest)

        assert finding.passed is False
        assert "xhigh x1" in finding.observed

    def test_a_corrupt_line_is_counted_rather_than_hidden(self, tmp_path: Path) -> None:
        manifest = load_manifest(_MANIFEST)
        root = self._transcripts(
            tmp_path,
            [
                '{"request": "{\\"model\\": \\"' + manifest.executor.model_id,
                {"request": {"model": "another-model"}, "response": "{}"},
            ],
        )

        finding = reasoning_finding(root, manifest)

        assert finding.passed is None
        assert "1 unparseable" in finding.observed

    def test_no_transcript_is_unverified_not_passed(self) -> None:
        finding = reasoning_finding(None, load_manifest(_MANIFEST))

        assert finding.passed is None


class TestCachingOffTheLedger:
    """Never a failure: the provider may simply not publish the figure."""

    def test_a_cached_read_passes(self) -> None:
        finding = caching_finding((_record(0), _record(40)))

        assert finding.passed is True
        assert "1 of 2" in finding.observed

    def test_all_zeros_is_unverified(self) -> None:
        assert caching_finding((_record(0), _record(0))).passed is None

    def test_no_calls_is_unverified(self) -> None:
        assert caching_finding(()).passed is None


def _report(*, digest: str, passed: bool) -> WiringReport:
    """A smoke's findings under *digest*, passing or not.

    Returns:
        The report.
    """
    return WiringReport(
        manifest_sha256=NotBlankStr(digest),
        checked_at=datetime(2026, 9, 1, tzinfo=UTC),
        findings=(
            WiringFinding(
                treatment=NotBlankStr("compaction"),
                expected="wired",
                observed="wired" if passed else "absent",
                passed=passed,
            ),
            WiringFinding(
                treatment=NotBlankStr("prompt caching"),
                expected="a cached read",
                observed="nothing reported",
                passed=None,
            ),
        ),
    )


class TestTheRecordingIsGated:
    """No smoke, another matrix's smoke, or a failed smoke each refuse."""

    def test_the_findings_round_trip(self, tmp_path: Path) -> None:
        written = _report(digest="sha256:" + "a" * 64, passed=True)

        write_wiring_report(written, smoke_dir(tmp_path))
        loaded = load_wiring_report(smoke_dir(tmp_path))

        assert loaded == written
        assert loaded.passed is True
        assert loaded.unverified == ("prompt caching",)
        assert (smoke_dir(tmp_path) / WIRING_REPORT_NAME).is_file()

    def test_no_smoke_refuses(self, tmp_path: Path) -> None:
        with pytest.raises(RecursionDepthSmokeRequiredError, match="run --smoke"):
            require_passing_smoke(tmp_path, manifest_sha256="sha256:" + "a" * 64)

    def test_another_matrix_s_smoke_refuses(self, tmp_path: Path) -> None:
        write_wiring_report(
            _report(digest="sha256:" + "a" * 64, passed=True), smoke_dir(tmp_path)
        )

        with pytest.raises(RecursionDepthSmokeRequiredError, match="needs its own"):
            require_passing_smoke(tmp_path, manifest_sha256="sha256:" + "b" * 64)

    def test_a_failed_smoke_refuses_and_names_the_treatment(
        self, tmp_path: Path
    ) -> None:
        write_wiring_report(
            _report(digest="sha256:" + "a" * 64, passed=False), smoke_dir(tmp_path)
        )

        with pytest.raises(RecursionDepthSmokeRequiredError, match="compaction"):
            require_passing_smoke(tmp_path, manifest_sha256="sha256:" + "a" * 64)

    def test_a_passing_smoke_is_handed_back_for_the_report(
        self, tmp_path: Path
    ) -> None:
        written = _report(digest="sha256:" + "a" * 64, passed=True)
        write_wiring_report(written, smoke_dir(tmp_path))

        assert (
            require_passing_smoke(tmp_path, manifest_sha256="sha256:" + "a" * 64)
            == written
        )

    def test_an_unverified_finding_does_not_fail_the_smoke(self) -> None:
        report = _report(digest="sha256:" + "a" * 64, passed=True)

        assert report.passed is True
        assert report.unverified == ("prompt caching",)
