"""Tests for L1/L2/L3 progressive disclosure models."""

import pytest
from pydantic import ValidationError

from synthorg.core.tool_disclosure import ToolL1Metadata, ToolL2Body, ToolL3Resource

# ── ToolL1Metadata ───────────────────────────────────────────────


@pytest.mark.unit
class TestToolL1Metadata:
    """Tests for ToolL1Metadata model."""

    def test_valid_construction(self) -> None:
        meta = ToolL1Metadata(
            name="read_file",
            short_description="Read a file from the workspace",
            category="file_system",
            typical_cost_tier="cheap",
        )
        assert meta.name == "read_file"
        assert meta.short_description == "Read a file from the workspace"
        assert meta.category == "file_system"
        assert meta.typical_cost_tier == "cheap"

    def test_frozen(self) -> None:
        meta = ToolL1Metadata(
            name="read_file",
            short_description="Read a file",
            category="file_system",
            typical_cost_tier="cheap",
        )
        with pytest.raises(ValidationError):
            meta.name = "modified"  # type: ignore[misc]

    def test_short_description_max_length(self) -> None:
        with pytest.raises(ValidationError, match="String should have at most 200"):
            ToolL1Metadata(
                name="tool",
                short_description="x" * 201,
                category="file_system",
                typical_cost_tier="cheap",
            )

    def test_short_description_at_max_length(self) -> None:
        meta = ToolL1Metadata(
            name="tool",
            short_description="x" * 200,
            category="file_system",
            typical_cost_tier="cheap",
        )
        assert len(meta.short_description) == 200

    def test_blank_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ToolL1Metadata(
                name="   ",
                short_description="desc",
                category="file_system",
                typical_cost_tier="cheap",
            )

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ToolL1Metadata(
                name="",
                short_description="desc",
                category="file_system",
                typical_cost_tier="cheap",
            )

    def test_invalid_cost_tier(self) -> None:
        with pytest.raises(ValidationError, match="Input should be"):
            ToolL1Metadata(
                name="tool",
                short_description="desc",
                category="file_system",
                typical_cost_tier="free",  # type: ignore[arg-type]
            )

    @pytest.mark.parametrize("tier", ["cheap", "medium", "expensive"])
    def test_all_cost_tiers(self, tier: str) -> None:
        meta = ToolL1Metadata(
            name="tool",
            short_description="desc",
            category="file_system",
            typical_cost_tier=tier,  # type: ignore[arg-type]
        )
        assert meta.typical_cost_tier == tier

    def test_a_parameter_cannot_be_both_required_and_optional(self) -> None:
        with pytest.raises(ValidationError, match="both required and optional"):
            ToolL1Metadata(
                name="t",
                short_description="d",
                category="c",
                typical_cost_tier="cheap",
                required_parameters=("path",),
                optional_parameters=("path", "limit"),
            )

    def test_blank_parameter_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ToolL1Metadata(
                name="t",
                short_description="d",
                category="c",
                typical_cost_tier="cheap",
                required_parameters=(" ",),
            )

    def test_blank_category_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ToolL1Metadata(
                name="tool",
                short_description="desc",
                category="  ",
                typical_cost_tier="cheap",
            )


# ── ToolL2Body ───────────────────────────────────────────────────


@pytest.mark.unit
class TestToolL2Body:
    """Tests for ToolL2Body model."""

    def test_valid_construction(self) -> None:
        body = ToolL2Body(
            full_description="Read a file from the workspace filesystem.",
            parameter_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            usage_examples=("read_file(path='/src/main.py')",),
            failure_modes=("File not found", "Permission denied"),
        )
        assert "Read a file" in body.full_description
        assert body.parameter_schema["type"] == "object"
        assert len(body.usage_examples) == 1
        assert len(body.failure_modes) == 2

    def test_defaults(self) -> None:
        body = ToolL2Body(full_description="A tool")
        assert body.parameter_schema == {}
        assert body.usage_examples == ()
        assert body.failure_modes == ()

    def test_frozen(self) -> None:
        body = ToolL2Body(full_description="A tool")
        with pytest.raises(ValidationError):
            body.full_description = "modified"  # type: ignore[misc]


# ── ToolL3Resource ───────────────────────────────────────────────


@pytest.mark.unit
class TestToolL3Resource:
    """Tests for ToolL3Resource model."""

    def test_valid_construction(self) -> None:
        content = "# Example trace\nStep 1: call tool\nStep 2: check result"
        resource = ToolL3Resource(
            resource_id="example-trace-1",
            content_type="markdown",
            content=content,
            size_bytes=len(content.encode()),
        )
        assert resource.resource_id == "example-trace-1"
        assert resource.content_type == "markdown"
        assert resource.content == content
        assert resource.size_bytes == len(content.encode())

    def test_size_bytes_mismatch_rejected(self) -> None:
        with pytest.raises(ValidationError, match="does not match"):
            ToolL3Resource(
                resource_id="res-1",
                content_type="code",
                content="hello",
                size_bytes=999,
            )

    def test_size_bytes_correct_for_unicode(self) -> None:
        content = "hello world"
        resource = ToolL3Resource(
            resource_id="res-1",
            content_type="code",
            content=content,
            size_bytes=len(content.encode()),
        )
        assert resource.size_bytes == 11

    def test_empty_content_zero_bytes(self) -> None:
        resource = ToolL3Resource(
            resource_id="res-1",
            content_type="schema",
            content="",
            size_bytes=0,
        )
        assert resource.size_bytes == 0

    def test_frozen(self) -> None:
        resource = ToolL3Resource(
            resource_id="res-1",
            content_type="markdown",
            content="x",
            size_bytes=1,
        )
        with pytest.raises(ValidationError):
            resource.content = "modified"  # type: ignore[misc]

    def test_blank_resource_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ToolL3Resource(
                resource_id="  ",
                content_type="code",
                content="x",
                size_bytes=1,
            )

    @pytest.mark.parametrize(
        "content_type",
        ["markdown", "code", "schema", "example_trace"],
    )
    def test_all_content_types(self, content_type: str) -> None:
        resource = ToolL3Resource(
            resource_id="res-1",
            content_type=content_type,  # type: ignore[arg-type]
            content="x",
            size_bytes=1,
        )
        assert resource.content_type == content_type

    def test_invalid_content_type(self) -> None:
        with pytest.raises(ValidationError, match="Input should be"):
            ToolL3Resource(
                resource_id="res-1",
                content_type="pdf",  # type: ignore[arg-type]
                content="x",
                size_bytes=1,
            )

    def test_negative_size_bytes_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ToolL3Resource(
                resource_id="res-1",
                content_type="code",
                content="",
                size_bytes=-1,
            )
