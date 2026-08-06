"""Unit tests for execution loop auto-selection."""

import pytest
import structlog.testing
from pydantic import ValidationError

from synthorg.core.task_enums import Complexity
from synthorg.engine.approval_gate import ApprovalGate
from synthorg.engine.compaction.protocol import CompactionCallback
from synthorg.engine.loop_selector import (
    DEFAULT_AUTO_LOOP_RULES,
    AutoLoopConfig,
    AutoLoopRule,
    LoopType,
    build_execution_loop,
    registered_loop_types,
    resolve_loop_type,
    select_loop_type,
)
from synthorg.engine.openhands.errors import OpenHandsUnavailableError
from synthorg.engine.react_loop import ReactLoop
from synthorg.engine.stagnation import StagnationDetector
from synthorg.observability.events.execution import EXECUTION_LOOP_NO_RULE_MATCH
from tests._shared import mock_of

# ── select_loop_type: default rules ─────────────────────────


@pytest.mark.unit
class TestSelectLoopType:
    """Every complexity defaults to react until measurement says otherwise."""

    @pytest.mark.parametrize(
        "complexity",
        [
            Complexity.SIMPLE,
            Complexity.MEDIUM,
            Complexity.COMPLEX,
            Complexity.EPIC,
        ],
    )
    def test_default_rules_select_react(self, complexity: Complexity) -> None:
        result = select_loop_type(
            complexity=complexity,
            rules=DEFAULT_AUTO_LOOP_RULES,
        )
        assert result == "react"

    def test_no_matching_rule_falls_back_to_react(self) -> None:
        """Empty rules tuple => fallback to react."""
        result = select_loop_type(
            complexity=Complexity.COMPLEX,
            rules=(),
        )
        assert result == "react"

    def test_no_matching_rule_logs_warning(self) -> None:
        """Fallback to default emits a warning log."""
        with structlog.testing.capture_logs() as logs:
            select_loop_type(
                complexity=Complexity.COMPLEX,
                rules=(),
            )
        events = [e for e in logs if e["event"] == EXECUTION_LOOP_NO_RULE_MATCH]
        assert len(events) == 1
        assert events[0]["complexity"] == "complex"
        assert events[0]["fallback"] == "react"

    def test_rule_mapping_to_react_does_not_warn(self) -> None:
        """When a rule explicitly maps to react, no NO_RULE_MATCH warning."""
        rules = (AutoLoopRule(complexity=Complexity.SIMPLE, loop_type=LoopType.REACT),)
        with structlog.testing.capture_logs() as logs:
            result = select_loop_type(
                complexity=Complexity.SIMPLE,
                rules=rules,
            )
        assert result is LoopType.REACT
        no_match_events = [
            e for e in logs if e["event"] == EXECUTION_LOOP_NO_RULE_MATCH
        ]
        assert len(no_match_events) == 0

    def test_custom_default_loop_type(self) -> None:
        """Empty rules with custom default_loop_type."""
        result = select_loop_type(
            complexity=Complexity.COMPLEX,
            rules=(),
            default_loop_type=LoopType.OPENHANDS,
        )
        assert result is LoopType.OPENHANDS

    def test_an_override_rule_wins_over_the_default(self) -> None:
        """An operator's measured override routes that complexity, not react."""
        rules = (
            AutoLoopRule(complexity=Complexity.COMPLEX, loop_type=LoopType.OPENHANDS),
        )
        assert (
            select_loop_type(complexity=Complexity.COMPLEX, rules=rules)
            is LoopType.OPENHANDS
        )


# ── Retired loop names ───────────────────────────────────────


@pytest.mark.unit
class TestResolveLoopType:
    """A stored value naming a deleted loop maps onto what runs instead.

    A setting is validated on write and never on read, so a row written while
    plan_execute / hybrid were valid outlives them.
    """

    @pytest.mark.parametrize("retired", ["plan_execute", "hybrid"])
    def test_retired_names_resolve_to_react(self, retired: str) -> None:
        assert resolve_loop_type(retired) == "react"

    @pytest.mark.parametrize("live", ["react", "openhands"])
    def test_live_names_pass_through(self, live: str) -> None:
        assert resolve_loop_type(live) == live

    def test_an_unknown_name_is_refused(self) -> None:
        """Only retired names are mapped; a typo is not quietly run as react."""
        with pytest.raises(ValueError, match="Unknown loop type 'typo'"):
            resolve_loop_type("typo")


# ── Registry ─────────────────────────────────────────────────


@pytest.mark.unit
class TestRegisteredLoopTypes:
    """The registry is the single source the A/B manifest validates against."""

    def test_only_react_and_openhands_ship(self) -> None:
        assert registered_loop_types() == ("openhands", "react")


# ── AutoLoopConfig model ─────────────────────────────────────


@pytest.mark.unit
class TestAutoLoopConfig:
    """Frozen Pydantic config model."""

    def test_defaults(self) -> None:
        config = AutoLoopConfig()
        assert config.rules == DEFAULT_AUTO_LOOP_RULES
        assert config.default_loop_type is LoopType.REACT

    def test_frozen(self) -> None:
        config = AutoLoopConfig()
        with pytest.raises(ValidationError):
            config.default_loop_type = LoopType.OPENHANDS  # type: ignore[misc]

    def test_custom_rules(self) -> None:
        rules = (
            AutoLoopRule(complexity=Complexity.SIMPLE, loop_type=LoopType.OPENHANDS),
            AutoLoopRule(complexity=Complexity.MEDIUM, loop_type=LoopType.REACT),
        )
        config = AutoLoopConfig(rules=rules)
        assert config.rules == rules

    def test_duplicate_complexity_rejected(self) -> None:
        """Rules with duplicate complexity values are invalid."""
        with pytest.raises(ValidationError, match="Duplicate complexity"):
            AutoLoopConfig(
                rules=(
                    AutoLoopRule(
                        complexity=Complexity.SIMPLE, loop_type=LoopType.REACT
                    ),
                    AutoLoopRule(
                        complexity=Complexity.SIMPLE, loop_type=LoopType.OPENHANDS
                    ),
                ),
            )

    @pytest.mark.parametrize("retired", ["plan_execute", "hybrid"])
    def test_retired_loop_type_rejected(self, retired: str) -> None:
        """The model stays strict; mapping happens at the settings read."""
        with pytest.raises(ValidationError):
            AutoLoopConfig(default_loop_type=retired)  # type: ignore[arg-type]

    def test_extra_fields_rejected(self) -> None:
        """Unknown config keys raise instead of being silently dropped."""
        with pytest.raises(ValidationError, match="extra"):
            AutoLoopConfig(nonexistent_key="value")  # type: ignore[call-arg]

    def test_unknown_default_loop_type_rejected(self) -> None:
        """default_loop_type must name a shipped loop."""
        with pytest.raises(ValidationError):
            AutoLoopConfig(default_loop_type="nonexistent")  # type: ignore[arg-type]

    def test_openhands_default_loop_type_accepted(self) -> None:
        """default_loop_type=openhands is valid since openhands is buildable."""
        config = AutoLoopConfig(
            rules=(
                AutoLoopRule(complexity=Complexity.SIMPLE, loop_type=LoopType.REACT),
            ),
            default_loop_type=LoopType.OPENHANDS,
        )
        assert config.default_loop_type is LoopType.OPENHANDS


# ── AutoLoopRule model ───────────────────────────────────────


@pytest.mark.unit
class TestAutoLoopRule:
    """Frozen Pydantic rule model."""

    def test_create(self) -> None:
        rule = AutoLoopRule(
            complexity=Complexity.SIMPLE,
            loop_type=LoopType.REACT,
        )
        assert rule.complexity == Complexity.SIMPLE
        assert rule.loop_type is LoopType.REACT

    def test_frozen(self) -> None:
        rule = AutoLoopRule(complexity=Complexity.SIMPLE, loop_type=LoopType.REACT)
        with pytest.raises(ValidationError):
            rule.loop_type = LoopType.OPENHANDS  # type: ignore[misc]

    def test_blank_loop_type_rejected(self) -> None:
        """The empty string names no loop."""
        with pytest.raises(ValidationError):
            AutoLoopRule(complexity=Complexity.SIMPLE, loop_type="")  # type: ignore[arg-type]

    def test_unknown_loop_type_rejected(self) -> None:
        """Unknown loop_type is rejected at rule construction."""
        with pytest.raises(ValidationError):
            AutoLoopRule(complexity=Complexity.SIMPLE, loop_type="typo")  # type: ignore[arg-type]

    @pytest.mark.parametrize("retired", ["plan_execute", "hybrid"])
    def test_retired_loop_type_rejected(self, retired: str) -> None:
        with pytest.raises(ValidationError):
            AutoLoopRule(complexity=Complexity.SIMPLE, loop_type=retired)  # type: ignore[arg-type]

    def test_extra_fields_rejected(self) -> None:
        """Unknown fields raise instead of being silently dropped."""
        with pytest.raises(ValidationError, match="extra"):
            AutoLoopRule(
                complexity=Complexity.SIMPLE,
                loop_type=LoopType.REACT,
                typo="value",  # type: ignore[call-arg]
            )


# ── build_execution_loop factory ─────────────────────────────


@pytest.mark.unit
class TestBuildExecutionLoop:
    """Factory creates correct loop instances."""

    def test_build_react(self) -> None:
        loop = build_execution_loop("react")
        assert isinstance(loop, ReactLoop)
        assert loop.get_loop_type() == "react"

    def test_build_react_with_gates(self) -> None:
        gate = mock_of[ApprovalGate]()
        detector = mock_of[StagnationDetector]()
        loop = build_execution_loop(
            "react",
            approval_gate=gate,
            stagnation_detector=detector,
        )
        assert isinstance(loop, ReactLoop)
        assert loop.approval_gate is gate
        assert loop.stagnation_detector is detector

    def test_build_react_with_compaction_callback(self) -> None:
        """ReactLoop receives compaction_callback when provided."""
        compact_cb = mock_of[CompactionCallback]()
        loop = build_execution_loop(
            "react",
            compaction_callback=compact_cb,
        )
        assert isinstance(loop, ReactLoop)
        assert loop.compaction_callback is compact_cb

    def test_build_openhands_without_deps_fails_loud(self) -> None:
        """The sandboxed loop names its unmet wiring rather than degrading."""
        with pytest.raises(OpenHandsUnavailableError, match="not wired"):
            build_execution_loop("openhands")

    @pytest.mark.parametrize("retired", ["plan_execute", "hybrid"])
    def test_retired_type_raises(self, retired: str) -> None:
        from synthorg.core.registry.errors import StrategyFactoryNotFoundError

        with pytest.raises(StrategyFactoryNotFoundError):
            build_execution_loop(retired)

    def test_unknown_type_raises(self) -> None:
        from synthorg.core.registry.errors import StrategyFactoryNotFoundError

        with pytest.raises(
            StrategyFactoryNotFoundError,
            match="No execution_loop factory registered",
        ):
            build_execution_loop("nonexistent")
