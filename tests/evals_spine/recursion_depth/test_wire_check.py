# module-kind: tests
"""The wire-level smoke: each treatment read off evidence, never off config.

What makes a finding honest is asserted on doubles: what it reads, when it
refuses to say anything, and that a recording cannot start without it.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from structlog.testing import capture_logs

from evals.errors import RecursionDepthSmokeRequiredError
from evals.harness.stall_watch import ProgressTrackingLedger
from evals.recursion_depth.manifest import Arm, StagnationTreatment, load_manifest
from evals.recursion_depth.models import WiringFinding, WiringReport
from evals.recursion_depth.session import LeafReview, SessionOutcome
from evals.recursion_depth.wire_check import (
    WIRING_REPORT_NAME,
    WiringProbe,
    budget_finding,
    caching_finding,
    compaction_finding,
    governance_findings,
    leaf_review_finding,
    load_wiring_report,
    matrix_digest,
    memory_finding,
    peer_review_finding,
    reasoning_finding,
    require_passing_smoke,
    routed_model_ids,
    smoke_dir,
    smoke_manifest,
    spec_identity,
    stagnation_finding,
    tool_surface_finding,
    write_wiring_report,
)
from synthorg.api.state import AppState
from synthorg.approval.state import ApprovalStateSlice
from synthorg.budget.cost_record import CostRecord
from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.config.schema import RootConfig
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.review_gate import ReviewGateService
from synthorg.engine.wiring_summary import EngineWiringSummary
from synthorg.memory.embedding.dispatch import format_model_ref
from synthorg.memory.state import MemoryStateSlice
from synthorg.observability.events.evals import EVALS_RECURSION_SMOKE_UNVERIFIED
from synthorg.settings.enums import SettingNamespace, SettingSource
from synthorg.settings.models import SettingValue
from synthorg.settings.service import SettingsService
from synthorg.settings.state import SettingsStateSlice
from tests._shared import make_app_state, mock_of

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
        "has_external_api_runtime": False,
        "cost_tracker": None,
    }
    base.update(overrides)
    return EngineWiringSummary(**base)  # type: ignore[arg-type]


def _record(cache_read: int, *, cache_write: int = 0) -> CostRecord:
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
        cache_write_input_tokens=cache_write,
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
        finding = tool_surface_finding(("read_file", "shell_command", "write_file"))

        assert finding.passed is True
        assert "shell_command" in finding.observed

    def test_a_run_that_recorded_no_surface_fails(self) -> None:
        finding = tool_surface_finding(None)

        assert finding.passed is False

    def test_an_empty_surface_fails(self) -> None:
        finding = tool_surface_finding(())

        assert finding.passed is False

    def test_the_declared_detector_must_be_the_one_watching(self) -> None:
        manifest = load_manifest(_MANIFEST)

        other = _wiring(stagnation_strategy="off_by_one")

        assert stagnation_finding(_wiring(), manifest).passed is True
        assert stagnation_finding(other, manifest).passed is False

    def test_a_matrix_declaring_no_detector_expects_none(self) -> None:
        manifest = load_manifest(_MANIFEST).model_copy(
            update={"stagnation": StagnationTreatment(strategy=NotBlankStr("off"))}
        )

        none = _wiring(has_stagnation_detector=False, stagnation_strategy=None)

        assert stagnation_finding(none, manifest).passed is True
        assert stagnation_finding(_wiring(), manifest).passed is False

    def test_the_budget_must_bound_the_cell_ledger_itself(self) -> None:
        ledger = ProgressTrackingLedger()

        same = budget_finding(_wiring(cost_tracker=ledger), ledger)
        other = budget_finding(_wiring(cost_tracker=ProgressTrackingLedger()), ledger)

        assert same.passed is True
        assert other.passed is False
        assert "another tracker" in other.observed

    def test_the_three_governance_seams_are_each_their_own_finding(self) -> None:
        findings = governance_findings(
            _wiring(has_policy_engine=False), configured_policy_engine="cedar"
        )

        verdicts = {finding.treatment: finding.passed for finding in findings}
        assert verdicts == {
            "review pipeline": True,
            "approval gate": True,
            "policy engine": False,
        }

    def test_the_policy_engine_is_expected_only_where_the_host_configured_one(
        self,
    ) -> None:
        # The product builds none by default; a smoke demanding one read
        # every default deployment as under-wired.
        absent_as_configured = governance_findings(
            _wiring(has_policy_engine=False), configured_policy_engine="none"
        )
        present_unconfigured = governance_findings(
            _wiring(has_policy_engine=True), configured_policy_engine="none"
        )

        policy = {f.treatment: f for f in absent_as_configured}["policy engine"]
        assert policy.passed is True
        assert "none" in policy.expected
        assert {f.treatment: f.passed for f in present_unconfigured}[
            "policy engine"
        ] is False


def _app_state(
    *,
    threshold: str,
    backend: object | None,
    embedder_ref: str | None,
    wiring_failure: str | None = None,
) -> AppState:
    """An application whose live settings and memory slice read as given.

    Returns:
        The state.
    """
    value = SettingValue(
        namespace=SettingNamespace.ENGINE,
        key=NotBlankStr("compaction_fill_threshold_percent"),
        value=threshold,
        source=SettingSource.DATABASE,
    )
    settings = mock_of[SettingsService](get=AsyncMock(return_value=value))
    return make_app_state(
        slices={
            SettingsStateSlice: {"settings_service": settings},
            MemoryStateSlice: {
                "backend": backend,
                "embedder_ref": embedder_ref,
                "wiring_failure": wiring_failure,
            },
        }
    )


class TestLiveSettingsFindings:
    """Read back through the same live settings the engine reads, not the manifest."""

    async def test_compaction_passes_on_a_wired_callback_at_the_armed_threshold(
        self,
    ) -> None:
        manifest = load_manifest(_MANIFEST)
        declared = manifest.compaction.fill_threshold_percent
        app_state = _app_state(threshold=str(declared), backend=None, embedder_ref=None)

        finding = await compaction_finding(_wiring(), app_state, manifest)

        assert finding.passed is True
        assert f"live threshold {declared}" in finding.observed

    async def test_compaction_fails_on_a_threshold_the_engine_did_not_get(
        self,
    ) -> None:
        manifest = load_manifest(_MANIFEST)
        app_state = _app_state(threshold="55.0", backend=None, embedder_ref=None)

        finding = await compaction_finding(_wiring(), app_state, manifest)

        assert finding.passed is False

    async def test_compaction_fails_on_an_absent_callback(self) -> None:
        manifest = load_manifest(_MANIFEST)
        declared = manifest.compaction.fill_threshold_percent
        app_state = _app_state(threshold=str(declared), backend=None, embedder_ref=None)

        finding = await compaction_finding(
            _wiring(has_compaction_callback=False), app_state, manifest
        )

        assert finding.passed is False
        assert "callback absent" in finding.observed

    async def test_compaction_fails_on_a_live_value_that_is_not_a_number(
        self,
    ) -> None:
        manifest = load_manifest(_MANIFEST)
        app_state = _app_state(threshold="eighty", backend=None, embedder_ref=None)

        finding = await compaction_finding(_wiring(), app_state, manifest)

        assert finding.passed is False

    def test_memory_passes_on_a_backend_bound_to_the_declared_embedder(self) -> None:
        # The reference is compared as the embedder port spells it; a live
        # smoke compared the settings serialisation against it and read a
        # correctly bound backend as bound to something else.
        manifest = load_manifest(_MANIFEST)
        declared = format_model_ref(
            manifest.embedder.provider, manifest.embedder.model_id
        )
        app_state = _app_state(
            threshold="80.0", backend=object(), embedder_ref=declared
        )

        finding = memory_finding(app_state, manifest)

        assert finding.passed is True

    def test_memory_fails_on_another_embedder(self) -> None:
        manifest = load_manifest(_MANIFEST)
        other = format_model_ref("example-provider", "example-other-001")
        app_state = _app_state(threshold="80.0", backend=object(), embedder_ref=other)

        finding = memory_finding(app_state, manifest)

        assert finding.passed is False

    def test_memory_fails_and_names_the_reason_with_no_backend(self) -> None:
        manifest = load_manifest(_MANIFEST)
        app_state = _app_state(
            threshold="80.0",
            backend=None,
            embedder_ref=None,
            wiring_failure="embedder unreachable",
        )

        finding = memory_finding(app_state, manifest)

        assert finding.passed is False
        assert "embedder unreachable" in finding.observed


class TestTheProbe:
    """What the smoke reads is the FIRST engine the cell built."""

    async def test_a_cell_that_built_no_engine_has_nothing_to_read(self) -> None:
        probe = WiringProbe(load_manifest(_MANIFEST))
        app_state = _app_state(threshold="80.0", backend=None, embedder_ref=None)

        with pytest.raises(RecursionDepthSmokeRequiredError, match="built no engine"):
            await probe.report(app_state, transcript_root=None, manifest_sha256="x")

    def test_the_first_engine_wins(self) -> None:
        probe = WiringProbe(load_manifest(_MANIFEST))
        first = mock_of[AgentEngine]()
        second = mock_of[AgentEngine]()
        ledger = ProgressTrackingLedger()

        probe.observe(first, ledger)
        probe.observe(second, ProgressTrackingLedger())

        assert probe._engine is first
        assert probe._ledger is ledger

    def test_the_first_session_wins(self) -> None:
        """The surface is a run's, not an engine's, so it is read off a session."""
        probe = WiringProbe(load_manifest(_MANIFEST))
        first = SessionOutcome(
            cost=0.0,
            tokens=1,
            turns=1,
            termination="completed",
            tool_surface=("read_file",),
        )
        second = SessionOutcome(
            cost=0.0, tokens=1, turns=1, termination="completed", tool_surface=()
        )

        probe.observe_session(first)
        probe.observe_session(second)

        assert probe._session is first


class TestTheMatrixDigest:
    """Keyed on the matrix that RUNS, so an override needs its own smoke."""

    def test_the_same_matrix_digests_the_same(self) -> None:
        manifest = load_manifest(_MANIFEST)

        assert matrix_digest(manifest) == matrix_digest(load_manifest(_MANIFEST))
        assert matrix_digest(manifest).startswith("sha256:")

    def test_an_override_that_changes_the_treatment_changes_the_digest(self) -> None:
        manifest = load_manifest(_MANIFEST)
        narrowed = manifest.model_copy(
            update={"contract_stage": not manifest.contract_stage}
        )

        assert matrix_digest(narrowed) != matrix_digest(manifest)

    def test_the_smoke_cell_is_not_the_matrix_it_gates(self) -> None:
        # The smoke narrows to one cell, and a recording keyed on THAT digest
        # could never match; the gate keys both on the operator's matrix.
        manifest = load_manifest(_MANIFEST)

        assert matrix_digest(smoke_manifest(manifest)) != matrix_digest(manifest)

    def test_the_spec_is_named_by_its_place_in_the_tree(self, tmp_path: Path) -> None:
        """Two checkouts of one tree spell one matrix."""
        first = tmp_path / "checkout-a" / "evals" / "spec" / "sqlcsv"
        second = tmp_path / "checkout-b" / "evals" / "spec" / "sqlcsv"

        assert spec_identity(
            str(first), repo_root=tmp_path / "checkout-a"
        ) == spec_identity(str(second), repo_root=tmp_path / "checkout-b")
        assert spec_identity(str(first), repo_root=tmp_path / "checkout-a") == (
            "evals/spec/sqlcsv"
        )

    def test_a_spec_outside_the_tree_keeps_its_own_path(self, tmp_path: Path) -> None:
        elsewhere = tmp_path / "elsewhere" / "spec"

        identity = spec_identity(str(elsewhere), repo_root=tmp_path / "checkout")

        assert identity == elsewhere.resolve().as_posix()

    def test_a_different_spec_is_a_different_matrix(self) -> None:
        manifest = load_manifest(_MANIFEST)
        other = manifest.model_copy(update={"spec_dir": manifest.spec_dir + "-other"})

        assert matrix_digest(other) != matrix_digest(manifest)


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

    def test_a_request_for_the_routed_id_counts(self, tmp_path: Path) -> None:
        # The manifest names the alias; the wire carries what it routes to.
        # Matched on the alias alone, every request of a live recording read
        # as absent and the finding stayed unverified.
        manifest = load_manifest(_MANIFEST)
        body = {"model": "upstream-model-9", "reasoning_effort": "high"}
        root = self._transcripts(
            tmp_path, [{"request": json.dumps(body), "response": "{}"}]
        )

        unrouted = reasoning_finding(root, manifest)
        routed = reasoning_finding(
            root, manifest, routed_ids=frozenset({"upstream-model-9"})
        )

        assert unrouted.passed is None
        assert routed.passed is True

    def test_routed_ids_come_from_the_provider_config(self) -> None:
        manifest = load_manifest(_MANIFEST)
        executor = manifest.executor
        config = RootConfig(
            company_name=NotBlankStr("Routed"),
            providers={
                executor.provider: ProviderConfig(
                    connection_name=NotBlankStr(executor.provider),
                    models=(
                        ProviderModelConfig(
                            id=NotBlankStr("upstream-model-9"),
                            alias=NotBlankStr(executor.model_id),
                        ),
                        ProviderModelConfig(id=NotBlankStr("unrelated-model")),
                    ),
                )
            },
        )

        assert routed_model_ids(config, executor) == {
            executor.model_id,
            "upstream-model-9",
        }

    def test_an_unknown_provider_routes_to_the_declared_id_alone(self) -> None:
        manifest = load_manifest(_MANIFEST)
        config = RootConfig(company_name=NotBlankStr("Empty"))

        assert routed_model_ids(config, manifest.executor) == {
            manifest.executor.model_id
        }


class TestCachingOffTheLedger:
    """Never a failure: the provider may simply not publish the figure."""

    def test_a_cached_read_after_the_first_call_passes(self) -> None:
        finding = caching_finding((_record(0), _record(40), _record(0)))

        assert finding.passed is True
        assert "1 of 2" in finding.observed

    def test_a_cached_read_on_the_first_call_alone_is_unverified(self) -> None:
        """A prefix an earlier cell left behind proves nothing about this one."""
        assert caching_finding((_record(40), _record(0))).passed is None

    def test_a_single_cached_call_is_unverified(self) -> None:
        assert caching_finding((_record(40),)).passed is None

    def test_a_read_after_this_cells_own_write_passes(self) -> None:
        finding = caching_finding((_record(0, cache_write=30), _record(40)))

        assert finding.passed is True

    def test_a_read_before_any_write_is_unverified_where_writes_are_reported(
        self,
    ) -> None:
        """Writes are reported, so a read with none before it is an earlier cell's."""
        records = (_record(0), _record(40), _record(0, cache_write=30))

        assert caching_finding(records).passed is None

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

        assert loaded is not None
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

    def test_an_unverified_finding_is_named_at_the_gate(self, tmp_path: Path) -> None:
        """The gate says which treatments it could not read, before the spend."""
        write_wiring_report(
            _report(digest="sha256:" + "a" * 64, passed=True), smoke_dir(tmp_path)
        )

        with capture_logs() as logs:
            require_passing_smoke(tmp_path, manifest_sha256="sha256:" + "a" * 64)

        unverified = [
            entry
            for entry in logs
            if entry["event"] == EVALS_RECURSION_SMOKE_UNVERIFIED
        ]
        assert len(unverified) == 1
        assert unverified[0]["treatments"] == ["prompt caching"]
        assert unverified[0]["log_level"] == "warning"


class TestPeerReviewFinding:
    """A wired pipeline is not an attached reviewer, and this reads the gate."""

    @staticmethod
    def _state(gate: ReviewGateService | None) -> AppState:
        return make_app_state(slices={ApprovalStateSlice: {"review_gate": gate}})

    def test_an_attached_gate_passes(self) -> None:
        gate = mock_of[ReviewGateService](completion_oracle_gate_attached=True)

        assert peer_review_finding(self._state(gate)).passed is True

    def test_a_review_gate_with_no_oracle_fails(self) -> None:
        # The shape a host with no coordination pair boots into: the build/test
        # gate alone judges every unit, and the engine's summary cannot tell.
        gate = mock_of[ReviewGateService](completion_oracle_gate_attached=False)

        finding = peer_review_finding(self._state(gate))

        assert finding.passed is False
        assert "no completion-oracle gate" in finding.observed

    def test_no_review_gate_at_all_fails(self) -> None:
        assert peer_review_finding(self._state(None)).passed is False


class TestLeafReviewFinding:
    """A pipeline PRESENT on the engine is not a leaf having been reviewed.

    Eight recordings carried a fully wired review pipeline that no leaf ever
    reached, because the host never held the leaf's task and the transition
    into review was refused; nothing in the wiring summary could tell.
    """

    def test_a_reviewed_leaf_passes(self) -> None:
        finding = leaf_review_finding(
            LeafReview(task_status="completed", verdict="approve")
        )

        assert finding.passed is True
        assert "approve" in finding.observed

    def test_a_leaf_the_host_never_held_fails(self) -> None:
        finding = leaf_review_finding(LeafReview(task_status=None, verdict=None))

        assert finding.passed is False
        assert "absent" in finding.observed

    def test_a_park_is_a_review_that_reached_the_leaf(self) -> None:
        # An escalation is the product's answer, not the harness's absence:
        # the row moved and a verdict was archived, so the path is wired.
        finding = leaf_review_finding(
            LeafReview(task_status="blocked", verdict="escalate")
        )

        assert finding.passed is True

    def test_no_finished_leaf_is_unverified(self) -> None:
        finding = leaf_review_finding(None)

        assert finding.passed is None

    def test_the_probe_keeps_the_first_leaf(self) -> None:
        probe = WiringProbe(load_manifest(_MANIFEST))
        probe.observe_leaf(LeafReview(task_status="completed", verdict="approve"))
        probe.observe_leaf(LeafReview(task_status=None, verdict=None))

        assert leaf_review_finding(probe._leaf).passed is True

    def test_a_leaf_that_stopped_short_is_unverified_not_absent(self) -> None:
        # The product routes a turn-capped run to FAILED and never offers it
        # to the pipeline, so the row moved (the host held it) and no verdict
        # could exist: neither a wiring gap nor proof the pipeline runs.
        finding = leaf_review_finding(LeafReview(task_status="failed", verdict=None))

        assert finding.passed is None
        assert "turn cap" in finding.observed

    def test_a_parked_leaf_with_no_verdict_is_unverified(self) -> None:
        finding = leaf_review_finding(LeafReview(task_status="blocked", verdict=None))

        assert finding.passed is None

    def test_a_leaf_parked_out_of_turns_is_unverified(self) -> None:
        # Every extension spent, the run parks for an operator to grant more
        # rather than failing; the pipeline was never asked, so nothing here
        # says whether it is wired.
        finding = leaf_review_finding(
            LeafReview(task_status="awaiting_input", verdict=None)
        )

        assert finding.passed is None

    def test_a_completed_leaf_with_no_verdict_still_fails(self) -> None:
        # Completed means the pipeline WAS asked, so no verdict is its absence.
        finding = leaf_review_finding(LeafReview(task_status="in_review", verdict=None))

        assert finding.passed is False

    def test_the_probe_prefers_a_leaf_that_reached_a_verdict(self) -> None:
        probe = WiringProbe(load_manifest(_MANIFEST))
        probe.observe_leaf(LeafReview(task_status="failed", verdict=None))
        probe.observe_leaf(LeafReview(task_status="completed", verdict="approve"))
        probe.observe_leaf(LeafReview(task_status="blocked", verdict="escalate"))

        assert probe._leaf == LeafReview(task_status="completed", verdict="approve")
