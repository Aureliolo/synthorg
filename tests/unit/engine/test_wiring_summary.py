"""The engine says what it was wired with, as a value rather than a log line."""

import dataclasses

import pytest

from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.dependencies import EngineLoopControls
from synthorg.engine.loop_protocol import ExecutionLoop
from synthorg.engine.stagnation.detector import ToolRepetitionDetector
from tests._shared import mock_of
from tests._shared.engine_deps import engine_deps
from tests._shared.scripted_provider import ScriptedProvider

pytestmark = pytest.mark.unit


class TestTheWiringSummary:
    """The same facts the creation event logs, readable by whoever built it."""

    def test_an_unwired_engine_reports_every_absence(self) -> None:
        engine = AgentEngine(engine_deps(ScriptedProvider()))

        wiring = engine.wiring

        assert wiring.has_compaction_callback is False
        assert wiring.has_stagnation_detector is False
        assert wiring.stagnation_strategy is None
        assert wiring.has_budget_enforcer is False
        assert wiring.has_review_pipeline is False
        assert wiring.has_memory_backend is False
        assert wiring.has_external_api_runtime is False

    def test_a_detector_is_named_rather_than_counted(self) -> None:
        deps = engine_deps(ScriptedProvider())
        controls = dataclasses.replace(
            deps.loop_controls, stagnation_detector=ToolRepetitionDetector()
        )
        engine = AgentEngine(dataclasses.replace(deps, loop_controls=controls))

        wiring = engine.wiring

        expected = ToolRepetitionDetector().get_detector_type()

        assert wiring.has_stagnation_detector is True
        assert wiring.stagnation_strategy == expected

    def test_an_injected_loop_reports_the_controls_it_never_consults(self) -> None:
        """A control bound beside a loop built elsewhere is held, not wired.

        The summary exists so a harness can ask what it measured; reporting a
        detector as present when the loop driving turns was constructed
        without it is the lie the record is for.
        """
        deps = engine_deps(ScriptedProvider())
        controls = dataclasses.replace(
            deps.loop_controls, stagnation_detector=ToolRepetitionDetector()
        )
        loop = mock_of[ExecutionLoop](get_loop_type=lambda: "injected")
        core = dataclasses.replace(deps.core, execution_loop=loop)
        engine = AgentEngine(
            dataclasses.replace(deps, core=core, loop_controls=controls)
        )

        wiring = engine.wiring

        assert wiring.loop_type == "injected"
        assert wiring.has_stagnation_detector is False
        assert wiring.stagnation_strategy is None
        assert wiring.has_compaction_callback is False
        assert wiring.has_approval_gate is False

    def test_the_log_fields_carry_no_object_reference(self) -> None:
        engine = AgentEngine(engine_deps(ScriptedProvider()))

        fields = engine.wiring.log_fields()

        assert "cost_tracker" not in fields
        assert fields["has_tool_registry"] is False

    def test_the_controls_bundle_is_what_the_summary_reads(self) -> None:
        """Pinned so a bundle rename cannot leave the summary reading a stale seam."""
        assert "stagnation_detector" in {
            field.name for field in dataclasses.fields(EngineLoopControls)
        }
