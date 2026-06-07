"""Tests for :mod:`synthorg.engine.workflow.subworkflow_binding`."""

from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from synthorg.engine.errors import SubworkflowIOError
from synthorg.engine.workflow.definition import WorkflowIODeclaration
from synthorg.engine.workflow.enums import WorkflowValueType
from synthorg.engine.workflow.subworkflow_binding import (
    project_output_bindings,
    resolve_input_bindings,
)


class TestResolveInputBindings:
    """resolve_input_bindings covers literals, lookups, defaults, type checks."""

    @pytest.mark.unit
    def test_literal_pass_through(self) -> None:
        decls = (WorkflowIODeclaration(name="quarter", type=WorkflowValueType.STRING),)
        resolved = resolve_input_bindings(
            bindings={"quarter": "Q4-2026"},
            parent_vars={},
            declarations=decls,
        )
        assert resolved == {"quarter": "Q4-2026"}

    @pytest.mark.unit
    def test_parent_dotted_path_lookup(self) -> None:
        decls = (WorkflowIODeclaration(name="quarter", type=WorkflowValueType.STRING),)
        resolved = resolve_input_bindings(
            bindings={"quarter": "@parent.current_quarter"},
            parent_vars={"current_quarter": "Q4"},
            declarations=decls,
        )
        assert resolved == {"quarter": "Q4"}

    @pytest.mark.unit
    def test_nested_dotted_path(self) -> None:
        decls = (WorkflowIODeclaration(name="quarter", type=WorkflowValueType.STRING),)
        resolved = resolve_input_bindings(
            bindings={"quarter": "@parent.context.quarter"},
            parent_vars={"context": {"quarter": "Q4"}},
            declarations=decls,
        )
        assert resolved == {"quarter": "Q4"}

    @pytest.mark.unit
    def test_missing_required_input_raises(self) -> None:
        decls = (WorkflowIODeclaration(name="quarter", type=WorkflowValueType.STRING),)
        with pytest.raises(SubworkflowIOError, match="Missing required input"):
            resolve_input_bindings(
                bindings={},
                parent_vars={},
                declarations=decls,
            )

    @pytest.mark.unit
    def test_unknown_binding_raises(self) -> None:
        decls = (WorkflowIODeclaration(name="quarter", type=WorkflowValueType.STRING),)
        with pytest.raises(SubworkflowIOError, match="Unknown input bindings"):
            resolve_input_bindings(
                bindings={"quarter": "Q4", "ghost": 42},
                parent_vars={},
                declarations=decls,
            )

    @pytest.mark.unit
    def test_optional_default_applied(self) -> None:
        decls = (
            WorkflowIODeclaration(
                name="budget",
                type=WorkflowValueType.FLOAT,
                required=False,
                default=1000.0,
            ),
        )
        resolved = resolve_input_bindings(
            bindings={},
            parent_vars={},
            declarations=decls,
        )
        assert resolved == {"budget": 1000.0}

    @pytest.mark.unit
    def test_type_mismatch_string_int(self) -> None:
        decls = (WorkflowIODeclaration(name="quarter", type=WorkflowValueType.STRING),)
        with pytest.raises(SubworkflowIOError, match="expects STRING"):
            resolve_input_bindings(
                bindings={"quarter": 42},
                parent_vars={},
                declarations=decls,
            )

    @pytest.mark.unit
    def test_type_mismatch_boolean_int(self) -> None:
        """Booleans are not accepted for INTEGER fields even though bool is int."""
        decls = (WorkflowIODeclaration(name="count", type=WorkflowValueType.INTEGER),)
        with pytest.raises(SubworkflowIOError, match="expects INTEGER"):
            resolve_input_bindings(
                bindings={"count": True},
                parent_vars={},
                declarations=decls,
            )

    @pytest.mark.unit
    def test_datetime_accepted(self) -> None:
        decls = (WorkflowIODeclaration(name="ts", type=WorkflowValueType.DATETIME),)
        now = datetime.now(UTC)
        resolved = resolve_input_bindings(
            bindings={"ts": now},
            parent_vars={},
            declarations=decls,
        )
        assert resolved == {"ts": now}

    @pytest.mark.unit
    def test_task_ref_rejects_blank(self) -> None:
        decls = (WorkflowIODeclaration(name="task", type=WorkflowValueType.TASK_REF),)
        with pytest.raises(SubworkflowIOError, match="expects TASK_REF"):
            resolve_input_bindings(
                bindings={"task": "  "},
                parent_vars={},
                declarations=decls,
            )

    @pytest.mark.unit
    def test_json_permissive(self) -> None:
        decls = (WorkflowIODeclaration(name="payload", type=WorkflowValueType.JSON),)
        resolved = resolve_input_bindings(
            bindings={"payload": {"a": [1, 2, 3]}},
            parent_vars={},
            declarations=decls,
        )
        assert resolved == {"payload": {"a": [1, 2, 3]}}

    @pytest.mark.unit
    def test_scoping_parent_key_outside_declarations_invisible(self) -> None:
        """Parent keys not referenced by a binding do not leak into resolved."""
        decls = (WorkflowIODeclaration(name="quarter", type=WorkflowValueType.STRING),)
        resolved = resolve_input_bindings(
            bindings={"quarter": "@parent.current_quarter"},
            parent_vars={"current_quarter": "Q4", "secret": "leaked"},
            declarations=decls,
        )
        assert "secret" not in resolved
        assert resolved == {"quarter": "Q4"}

    @pytest.mark.unit
    def test_missing_parent_key_raises(self) -> None:
        decls = (WorkflowIODeclaration(name="quarter", type=WorkflowValueType.STRING),)
        with pytest.raises(SubworkflowIOError, match="missing segment"):
            resolve_input_bindings(
                bindings={"quarter": "@parent.missing"},
                parent_vars={},
                declarations=decls,
            )

    @pytest.mark.unit
    @given(
        quarter=st.text(min_size=1, max_size=10).filter(
            lambda s: not s.startswith("@"),
        ),
    )
    def test_literal_strings_round_trip(self, quarter: str) -> None:
        decls = (WorkflowIODeclaration(name="quarter", type=WorkflowValueType.STRING),)
        resolved = resolve_input_bindings(
            bindings={"quarter": quarter},
            parent_vars={},
            declarations=decls,
        )
        assert resolved["quarter"] == quarter


class TestProjectOutputBindings:
    """project_output_bindings covers the reverse path."""

    @pytest.mark.unit
    def test_child_dotted_path(self) -> None:
        decls = (
            WorkflowIODeclaration(
                name="closing_report",
                type=WorkflowValueType.STRING,
            ),
        )
        projected = project_output_bindings(
            bindings={"closing_report": "@child.report"},
            child_vars={"report": "Q4 closed"},
            declarations=decls,
        )
        assert projected == {"closing_report": "Q4 closed"}

    @pytest.mark.unit
    def test_literal_output(self) -> None:
        decls = (
            WorkflowIODeclaration(
                name="status",
                type=WorkflowValueType.STRING,
            ),
        )
        projected = project_output_bindings(
            bindings={"status": "ok"},
            child_vars={},
            declarations=decls,
        )
        assert projected == {"status": "ok"}

    @pytest.mark.unit
    def test_missing_required_output_raises(self) -> None:
        decls = (
            WorkflowIODeclaration(
                name="status",
                type=WorkflowValueType.STRING,
            ),
        )
        with pytest.raises(
            SubworkflowIOError,
            match="Missing required output",
        ):
            project_output_bindings(
                bindings={},
                child_vars={},
                declarations=decls,
            )

    @pytest.mark.unit
    def test_unknown_output_binding_raises(self) -> None:
        decls = (
            WorkflowIODeclaration(
                name="status",
                type=WorkflowValueType.STRING,
            ),
        )
        with pytest.raises(
            SubworkflowIOError,
            match="Unknown output bindings",
        ):
            project_output_bindings(
                bindings={"ghost": "x"},
                child_vars={},
                declarations=decls,
            )

    @pytest.mark.unit
    def test_output_type_mismatch(self) -> None:
        decls = (
            WorkflowIODeclaration(
                name="count",
                type=WorkflowValueType.INTEGER,
            ),
        )
        with pytest.raises(SubworkflowIOError, match="expects INTEGER"):
            project_output_bindings(
                bindings={"count": "not a number"},
                child_vars={},
                declarations=decls,
            )

    @pytest.mark.unit
    def test_parent_pass_through_binding(self) -> None:
        decls = (
            WorkflowIODeclaration(
                name="context",
                type=WorkflowValueType.STRING,
            ),
        )
        projected = project_output_bindings(
            bindings={"context": "@parent.env"},
            child_vars={},
            declarations=decls,
            parent_vars={"env": "production"},
        )
        assert projected == {"context": "production"}


class TestBooleanAndAgentRefTypes:
    """Cover BOOLEAN and AGENT_REF type validation paths."""

    @pytest.mark.unit
    def test_boolean_input_accepted(self) -> None:
        decls = (WorkflowIODeclaration(name="flag", type=WorkflowValueType.BOOLEAN),)
        resolved = resolve_input_bindings(
            bindings={"flag": True},
            parent_vars={},
            declarations=decls,
        )
        assert resolved["flag"] is True

    @pytest.mark.unit
    def test_boolean_rejects_int(self) -> None:
        decls = (WorkflowIODeclaration(name="flag", type=WorkflowValueType.BOOLEAN),)
        with pytest.raises(SubworkflowIOError, match="expects BOOLEAN"):
            resolve_input_bindings(
                bindings={"flag": 1},
                parent_vars={},
                declarations=decls,
            )

    @pytest.mark.unit
    def test_agent_ref_accepted(self) -> None:
        decls = (WorkflowIODeclaration(name="agent", type=WorkflowValueType.AGENT_REF),)
        resolved = resolve_input_bindings(
            bindings={"agent": "agent-001"},
            parent_vars={},
            declarations=decls,
        )
        assert resolved["agent"] == "agent-001"

    @pytest.mark.unit
    def test_agent_ref_rejects_blank(self) -> None:
        decls = (WorkflowIODeclaration(name="agent", type=WorkflowValueType.AGENT_REF),)
        with pytest.raises(SubworkflowIOError, match="expects AGENT_REF"):
            resolve_input_bindings(
                bindings={"agent": "   "},
                parent_vars={},
                declarations=decls,
            )

    @pytest.mark.unit
    def test_boolean_output_accepted(self) -> None:
        decls = (WorkflowIODeclaration(name="ok", type=WorkflowValueType.BOOLEAN),)
        projected = project_output_bindings(
            bindings={"ok": "@child.success"},
            child_vars={"success": False},
            declarations=decls,
        )
        assert projected["ok"] is False

    @pytest.mark.unit
    def test_agent_ref_output_accepted(self) -> None:
        decls = (WorkflowIODeclaration(name="agent", type=WorkflowValueType.AGENT_REF),)
        projected = project_output_bindings(
            bindings={"agent": "@child.assigned"},
            child_vars={"assigned": "agent-002"},
            declarations=decls,
        )
        assert projected["agent"] == "agent-002"
