"""Tests for typed self-editing-memory tool argument models."""

import pytest
from pydantic import ValidationError

from synthorg.core.enums import MemoryCategory
from synthorg.memory.self_editing_args import (
    ArchivalMemorySearchArgs,
    ArchivalMemoryWriteArgs,
    CoreMemoryReadArgs,
    CoreMemoryWriteArgs,
    RecallMemoryReadArgs,
    RecallMemoryWriteArgs,
    SelfEditingArgs,
    parse_self_editing_args,
)


class TestCoreMemoryReadArgs:
    """Args for ``core_memory_read``."""

    @pytest.mark.unit
    def test_construction(self) -> None:
        args = CoreMemoryReadArgs()
        assert args.tool == "core_memory_read"

    @pytest.mark.unit
    def test_extra_keys_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            CoreMemoryReadArgs.model_validate(
                {"tool": "core_memory_read", "smuggled": "field"},
            )


class TestCoreMemoryWriteArgs:
    """Args for ``core_memory_write``."""

    @pytest.mark.unit
    def test_construction(self) -> None:
        args = CoreMemoryWriteArgs(content="remember this")
        assert args.tool == "core_memory_write"
        assert args.content == "remember this"

    @pytest.mark.unit
    def test_blank_content_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CoreMemoryWriteArgs(content="   ")

    @pytest.mark.unit
    def test_oversized_content_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CoreMemoryWriteArgs(content="x" * 50_001)

    @pytest.mark.unit
    def test_missing_content_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CoreMemoryWriteArgs.model_validate({"tool": "core_memory_write"})


class TestArchivalMemorySearchArgs:
    """Args for ``archival_memory_search``."""

    @pytest.mark.unit
    def test_minimal_construction(self) -> None:
        args = ArchivalMemorySearchArgs(query="find this")
        assert args.tool == "archival_memory_search"
        assert args.query == "find this"
        assert args.category is None
        assert args.limit is None

    @pytest.mark.unit
    def test_with_category_and_limit(self) -> None:
        args = ArchivalMemorySearchArgs(
            query="recent decisions",
            category=MemoryCategory.SEMANTIC,
            limit=20,
        )
        assert args.category is MemoryCategory.SEMANTIC
        assert args.limit == 20

    @pytest.mark.unit
    def test_invalid_category_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ArchivalMemorySearchArgs.model_validate(
                {
                    "tool": "archival_memory_search",
                    "query": "x",
                    "category": "not_a_real_category",
                },
            )

    @pytest.mark.unit
    def test_zero_limit_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ArchivalMemorySearchArgs(query="x", limit=0)

    @pytest.mark.unit
    def test_negative_limit_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ArchivalMemorySearchArgs(query="x", limit=-1)

    @pytest.mark.unit
    def test_blank_query_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ArchivalMemorySearchArgs(query="   ")


class TestArchivalMemoryWriteArgs:
    """Args for ``archival_memory_write``."""

    @pytest.mark.unit
    def test_construction(self) -> None:
        args = ArchivalMemoryWriteArgs(
            content="learned today",
            category=MemoryCategory.EPISODIC,
        )
        assert args.tool == "archival_memory_write"
        assert args.category is MemoryCategory.EPISODIC

    @pytest.mark.unit
    def test_missing_category_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ArchivalMemoryWriteArgs.model_validate(
                {"tool": "archival_memory_write", "content": "x"},
            )

    @pytest.mark.unit
    def test_oversized_content_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ArchivalMemoryWriteArgs(
                content="x" * 50_001,
                category=MemoryCategory.SEMANTIC,
            )


class TestRecallMemoryReadArgs:
    """Args for ``recall_memory_read``."""

    @pytest.mark.unit
    def test_construction(self) -> None:
        args = RecallMemoryReadArgs(memory_id="mem-1")
        assert args.tool == "recall_memory_read"
        assert args.memory_id == "mem-1"

    @pytest.mark.unit
    def test_oversized_memory_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RecallMemoryReadArgs(memory_id="x" * 257)

    @pytest.mark.unit
    def test_blank_memory_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RecallMemoryReadArgs(memory_id="  ")


class TestRecallMemoryWriteArgs:
    """Args for ``recall_memory_write``."""

    @pytest.mark.unit
    def test_construction(self) -> None:
        args = RecallMemoryWriteArgs(content="event happened")
        assert args.tool == "recall_memory_write"
        assert args.content == "event happened"

    @pytest.mark.unit
    def test_oversized_content_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RecallMemoryWriteArgs(content="x" * 50_001)


class TestParseSelfEditingArgs:
    """The dispatch-facing helper routes by ``tool_name``."""

    @pytest.mark.unit
    def test_routes_each_tool_to_its_variant(self) -> None:
        cases: list[tuple[str, dict[str, object], type]] = [
            ("core_memory_read", {}, CoreMemoryReadArgs),
            ("core_memory_write", {"content": "x"}, CoreMemoryWriteArgs),
            (
                "archival_memory_search",
                {"query": "x"},
                ArchivalMemorySearchArgs,
            ),
            (
                "archival_memory_write",
                {"content": "x", "category": "semantic"},
                ArchivalMemoryWriteArgs,
            ),
            ("recall_memory_read", {"memory_id": "m1"}, RecallMemoryReadArgs),
            ("recall_memory_write", {"content": "x"}, RecallMemoryWriteArgs),
        ]
        for tool, args_dict, expected_cls in cases:
            args = parse_self_editing_args(tool, args_dict)
            assert isinstance(args, expected_cls), (
                f"{tool}: expected {expected_cls.__name__}, got {type(args).__name__}"
            )

    @pytest.mark.unit
    def test_unknown_tool_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_self_editing_args("nonexistent_tool", {})

    @pytest.mark.unit
    def test_envelope_tool_overrides_arguments_tool_key(self) -> None:
        """The dispatch tool name wins over any ``tool`` key inside arguments."""
        # LLM tries to smuggle a different tool inside arguments while
        # the dispatcher routed it as core_memory_write.
        args = parse_self_editing_args(
            "core_memory_write",
            {"tool": "recall_memory_read", "content": "x"},
        )
        assert isinstance(args, CoreMemoryWriteArgs)

    @pytest.mark.unit
    def test_missing_required_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_self_editing_args("archival_memory_write", {"content": "x"})

    @pytest.mark.unit
    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_self_editing_args(
                "core_memory_write",
                {"content": "x", "smuggled": "field"},
            )

    @pytest.mark.unit
    def test_multiple_errors_returned(self) -> None:
        """A payload that violates multiple fields surfaces every error.

        Locks in the "complete validation errors" contract: a single
        ``parse_self_editing_args`` call must not stop after the first
        failure -- callers (LLMs, MCP clients) need every problem at
        once so they can correct in a single iteration.
        """
        with pytest.raises(ValidationError) as exc_info:
            parse_self_editing_args(
                "archival_memory_write",
                # ``content`` blank AND ``category`` missing AND
                # ``smuggled`` is an extra field -- three independent
                # violations.
                {"content": "   ", "smuggled": "field"},
            )
        errors = exc_info.value.errors()
        assert len(errors) >= 2, (
            f"expected multiple errors, got {len(errors)}: {errors}"
        )


class TestSelfEditingUnion:
    """Union covers exactly the six tools."""

    @pytest.mark.unit
    def test_union_covers_six_tools_exactly(self) -> None:
        from typing import get_args

        union_alias, _discriminator = get_args(SelfEditingArgs)
        variants = get_args(union_alias)
        tools = {v.model_fields["tool"].default for v in variants}
        assert tools == {
            "core_memory_read",
            "core_memory_write",
            "archival_memory_search",
            "archival_memory_write",
            "recall_memory_read",
            "recall_memory_write",
        }

    @pytest.mark.unit
    def test_every_variant_is_frozen(self) -> None:
        from typing import get_args

        union_alias, _discriminator = get_args(SelfEditingArgs)
        for variant in get_args(union_alias):
            assert variant.model_config.get("frozen") is True

    @pytest.mark.unit
    def test_every_variant_forbids_extras(self) -> None:
        from typing import get_args

        union_alias, _discriminator = get_args(SelfEditingArgs)
        for variant in get_args(union_alias):
            assert variant.model_config.get("extra") == "forbid"
