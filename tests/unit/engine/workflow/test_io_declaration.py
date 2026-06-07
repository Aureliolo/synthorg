"""Tests for ``WorkflowIODeclaration`` and ``WorkflowValueType``.

Covers the typed I/O contract model used by subworkflows, including
name uniqueness, default compatibility, and semver validation on
``WorkflowDefinition``.
"""

import pytest
from pydantic import ValidationError

from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowIODeclaration,
)
from synthorg.engine.workflow.enums import WorkflowValueType
from tests.unit.engine.workflow.conftest import (
    make_edge,
    make_end_node,
    make_start_node,
    make_task_node,
)


def _minimal_kwargs(**overrides: object) -> dict[str, object]:
    """Build the kwargs for a minimal valid workflow definition."""
    defaults: dict[str, object] = {
        "id": "wf-1",
        "name": "Test Workflow",
        "created_by": "test-user",
        "nodes": (make_start_node(), make_task_node(), make_end_node()),
        "edges": (
            make_edge("e1", "start-1", "task-1"),
            make_edge("e2", "task-1", "end-1"),
        ),
    }
    defaults.update(overrides)
    return defaults


class TestWorkflowIODeclaration:
    """WorkflowIODeclaration validation."""

    @pytest.mark.unit
    def test_basic_required_declaration(self) -> None:
        decl = WorkflowIODeclaration(
            name="quarter",
            type=WorkflowValueType.STRING,
        )
        assert decl.name == "quarter"
        assert decl.type is WorkflowValueType.STRING
        assert decl.required is True
        assert decl.default is None
        assert decl.description == ""

    @pytest.mark.unit
    def test_optional_with_default(self) -> None:
        decl = WorkflowIODeclaration(
            name="budget",
            type=WorkflowValueType.FLOAT,
            required=False,
            default=1000.0,
            description="Quarter budget cap",
        )
        assert decl.required is False
        assert decl.default == 1000.0

    @pytest.mark.unit
    def test_required_with_default_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="required declarations must not carry a default",
        ):
            WorkflowIODeclaration(
                name="budget",
                type=WorkflowValueType.FLOAT,
                required=True,
                default=0.0,
            )

    @pytest.mark.unit
    def test_blank_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowIODeclaration(name="  ", type=WorkflowValueType.STRING)

    @pytest.mark.unit
    def test_frozen(self) -> None:
        decl = WorkflowIODeclaration(name="x", type=WorkflowValueType.STRING)
        with pytest.raises(ValidationError, match="frozen"):
            decl.name = "y"  # type: ignore[misc]

    @pytest.mark.unit
    @pytest.mark.parametrize("value_type", list(WorkflowValueType))
    def test_every_value_type_accepted(
        self,
        value_type: WorkflowValueType,
    ) -> None:
        decl = WorkflowIODeclaration(name="v", type=value_type)
        assert decl.type is value_type


class TestWorkflowDefinitionSemver:
    """WorkflowDefinition semver field."""

    @pytest.mark.unit
    def test_default_version(self) -> None:
        wf = WorkflowDefinition.model_validate(_minimal_kwargs())
        assert wf.version == "1.0.0"
        assert wf.revision == 1
        assert wf.is_subworkflow is False
        assert wf.inputs == ()
        assert wf.outputs == ()

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "version_str",
        ["0.1.0", "1.2.3", "10.0.0", "1.10.0", "0.0.1"],
    )
    def test_valid_semver_accepted(self, version_str: str) -> None:
        wf = WorkflowDefinition.model_validate(
            _minimal_kwargs(version=version_str),
        )
        assert wf.version == version_str

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "version_str",
        ["not-a-version", "x.y.z", "abc"],
    )
    def test_invalid_semver_rejected(self, version_str: str) -> None:
        with pytest.raises(ValidationError, match="Invalid version"):
            WorkflowDefinition.model_validate(
                _minimal_kwargs(version=version_str),
            )

    @pytest.mark.unit
    def test_blank_version_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowDefinition.model_validate(_minimal_kwargs(version="  "))


class TestWorkflowDefinitionIODeclarations:
    """WorkflowDefinition inputs / outputs."""

    @pytest.mark.unit
    def test_inputs_and_outputs_round_trip(self) -> None:
        inputs = (
            WorkflowIODeclaration(name="quarter", type=WorkflowValueType.STRING),
            WorkflowIODeclaration(
                name="budget",
                type=WorkflowValueType.FLOAT,
                required=False,
                default=1000.0,
            ),
        )
        outputs = (
            WorkflowIODeclaration(
                name="closing_report",
                type=WorkflowValueType.STRING,
            ),
        )
        wf = WorkflowDefinition.model_validate(
            _minimal_kwargs(
                inputs=inputs,
                outputs=outputs,
                is_subworkflow=True,
            ),
        )
        assert wf.inputs == inputs
        assert wf.outputs == outputs
        assert wf.is_subworkflow is True

    @pytest.mark.unit
    def test_duplicate_input_names_rejected(self) -> None:
        inputs = (
            WorkflowIODeclaration(name="x", type=WorkflowValueType.STRING),
            WorkflowIODeclaration(name="x", type=WorkflowValueType.INTEGER),
        )
        with pytest.raises(ValidationError, match="Duplicate input"):
            WorkflowDefinition.model_validate(_minimal_kwargs(inputs=inputs))

    @pytest.mark.unit
    def test_duplicate_output_names_rejected(self) -> None:
        outputs = (
            WorkflowIODeclaration(name="y", type=WorkflowValueType.STRING),
            WorkflowIODeclaration(name="y", type=WorkflowValueType.INTEGER),
        )
        with pytest.raises(ValidationError, match="Duplicate output"):
            WorkflowDefinition.model_validate(_minimal_kwargs(outputs=outputs))

    @pytest.mark.unit
    def test_input_and_output_can_share_name(self) -> None:
        """Input names and output names are validated independently."""
        inputs = (WorkflowIODeclaration(name="q", type=WorkflowValueType.STRING),)
        outputs = (WorkflowIODeclaration(name="q", type=WorkflowValueType.STRING),)
        wf = WorkflowDefinition.model_validate(
            _minimal_kwargs(inputs=inputs, outputs=outputs),
        )
        assert wf.inputs[0].name == "q"
        assert wf.outputs[0].name == "q"
