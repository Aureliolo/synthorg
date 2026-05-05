"""Unit tests for ``engine.workflow.subworkflow_models``.

Locks in the frozen / shape contract so a future change to either
DTO must update these assertions deliberately.
"""

import pytest
from pydantic import ValidationError

from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.subworkflow_models import (
    ParentReference,
    SubworkflowSummary,
)

pytestmark = pytest.mark.unit


def _summary() -> SubworkflowSummary:
    return SubworkflowSummary(
        subworkflow_id=NotBlankStr("sub-x"),
        latest_version=NotBlankStr("1.2.3"),
        name=NotBlankStr("Subworkflow X"),
        description="example",
        input_count=2,
        output_count=1,
        version_count=3,
    )


def _parent() -> ParentReference:
    return ParentReference(
        parent_id=NotBlankStr("wf-parent"),
        parent_name=NotBlankStr("Parent WF"),
        pinned_version=NotBlankStr("1.2.3"),
        node_id=NotBlankStr("node-7"),
        parent_type="workflow_definition",
    )


class TestSubworkflowSummary:
    """Shape and immutability contract for ``SubworkflowSummary``."""

    def test_constructs_with_required_fields(self) -> None:
        summary = _summary()
        assert summary.subworkflow_id == "sub-x"
        assert summary.version_count == 3

    def test_is_frozen(self) -> None:
        summary = _summary()
        with pytest.raises(ValidationError):
            summary.description = "mutated"  # type: ignore[misc]

    def test_rejects_negative_counts(self) -> None:
        with pytest.raises(ValidationError):
            SubworkflowSummary(
                subworkflow_id=NotBlankStr("sub-x"),
                latest_version=NotBlankStr("1.0.0"),
                name=NotBlankStr("X"),
                input_count=-1,
                output_count=0,
                version_count=1,
            )

    def test_version_count_minimum_is_one(self) -> None:
        with pytest.raises(ValidationError):
            SubworkflowSummary(
                subworkflow_id=NotBlankStr("sub-x"),
                latest_version=NotBlankStr("1.0.0"),
                name=NotBlankStr("X"),
                input_count=0,
                output_count=0,
                version_count=0,
            )


class TestParentReference:
    """Shape and discriminator contract for ``ParentReference``."""

    def test_workflow_definition_parent_omits_version(self) -> None:
        ref = _parent()
        assert ref.parent_type == "workflow_definition"
        assert ref.parent_version is None

    def test_subworkflow_parent_carries_its_own_version(self) -> None:
        ref = ParentReference(
            parent_id=NotBlankStr("sub-parent"),
            parent_name=NotBlankStr("Parent Sub"),
            pinned_version=NotBlankStr("1.0.0"),
            node_id=NotBlankStr("node-1"),
            parent_type="subworkflow",
            parent_version=NotBlankStr("0.5.0"),
        )
        assert ref.parent_version == "0.5.0"

    def test_is_frozen(self) -> None:
        ref = _parent()
        with pytest.raises(ValidationError):
            ref.pinned_version = NotBlankStr("9.9.9")  # type: ignore[misc]

    def test_parent_type_literal_rejects_other_values(self) -> None:
        with pytest.raises(ValidationError):
            ParentReference(
                parent_id=NotBlankStr("wf-parent"),
                parent_name=NotBlankStr("Parent"),
                pinned_version=NotBlankStr("1.0.0"),
                node_id=NotBlankStr("node-7"),
                parent_type="meeting",  # type: ignore[arg-type]
            )
