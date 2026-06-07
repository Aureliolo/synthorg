"""Tests for memory tool-wrapper + ontology arg models."""

import pytest
from pydantic import ValidationError

from synthorg.core.memory_enums import MemoryCategory
from synthorg.memory.tools._args import (
    KnowledgeArchitectBrowseWikiArgs,
    KnowledgeArchitectDeleteArgs,
    KnowledgeArchitectGuideArgs,
    KnowledgeArchitectReadArgs,
    KnowledgeArchitectSearchArgs,
    KnowledgeArchitectWriteArgs,
    RecallMemoryArgs,
    RecallMemoryReadArgs,
    RecallMemoryWriteArgs,
    SearchMemoryArgs,
)
from synthorg.ontology.injection._tool_args import LookupEntityArgs


class TestSearchMemoryArgs:
    @pytest.mark.unit
    def test_minimal(self) -> None:
        args = SearchMemoryArgs(query="x")
        assert args.limit == 10
        assert args.categories == ()

    @pytest.mark.unit
    def test_with_categories(self) -> None:
        args = SearchMemoryArgs(
            query="x",
            categories=(MemoryCategory.SEMANTIC, MemoryCategory.EPISODIC),
        )
        assert MemoryCategory.SEMANTIC in args.categories

    @pytest.mark.unit
    def test_limit_bounds(self) -> None:
        with pytest.raises(ValidationError):
            SearchMemoryArgs(query="x", limit=0)
        with pytest.raises(ValidationError):
            SearchMemoryArgs(query="x", limit=51)


class TestRecallMemoryArgs:
    @pytest.mark.unit
    def test_construction(self) -> None:
        args = RecallMemoryArgs(memory_id="mem-1")
        assert args.memory_id == "mem-1"

    @pytest.mark.unit
    def test_oversized_memory_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RecallMemoryArgs(memory_id="x" * 257)


class TestRecallReadWrite:
    @pytest.mark.unit
    def test_read(self) -> None:
        args = RecallMemoryReadArgs(memory_id="m1")
        assert args.memory_id == "m1"

    @pytest.mark.unit
    def test_write_size_cap(self) -> None:
        with pytest.raises(ValidationError):
            RecallMemoryWriteArgs(content="x" * 50_001)


class TestKnowledgeArchitectArgs:
    @pytest.mark.unit
    def test_guide_no_fields(self) -> None:
        args = KnowledgeArchitectGuideArgs()
        assert args.model_dump() == {}

    @pytest.mark.unit
    def test_search_defaults(self) -> None:
        args = KnowledgeArchitectSearchArgs(query="x")
        assert args.limit == 10
        assert args.category is None

    @pytest.mark.unit
    def test_search_limit_bounds(self) -> None:
        with pytest.raises(ValidationError):
            KnowledgeArchitectSearchArgs(query="x", limit=0)
        with pytest.raises(ValidationError):
            KnowledgeArchitectSearchArgs(query="x", limit=101)

    @pytest.mark.unit
    def test_read_requires_entry_id(self) -> None:
        with pytest.raises(ValidationError):
            KnowledgeArchitectReadArgs.model_validate({})

    @pytest.mark.unit
    def test_write_constraints(self) -> None:
        KnowledgeArchitectWriteArgs(content="x", category="policy")
        with pytest.raises(ValidationError):
            KnowledgeArchitectWriteArgs(content="x" * 100_001, category="policy")
        with pytest.raises(ValidationError):
            KnowledgeArchitectWriteArgs(
                content="x",
                category="policy",
                tags=tuple(f"tag{i}" for i in range(51)),
            )

    @pytest.mark.unit
    def test_delete(self) -> None:
        args = KnowledgeArchitectDeleteArgs(entry_id="e1")
        assert args.entry_id == "e1"

    @pytest.mark.unit
    def test_browse_wiki_default(self) -> None:
        args = KnowledgeArchitectBrowseWikiArgs()
        assert args.include_raw is False


class TestLookupEntityArgs:
    @pytest.mark.unit
    def test_name_only(self) -> None:
        args = LookupEntityArgs(name="Task")
        assert args.name == "Task"
        assert args.query is None

    @pytest.mark.unit
    def test_query_only(self) -> None:
        args = LookupEntityArgs(query="approval")
        assert args.query == "approval"

    @pytest.mark.unit
    def test_both_rejected(self) -> None:
        with pytest.raises(ValidationError, match="exactly one"):
            LookupEntityArgs(name="Task", query="approval")

    @pytest.mark.unit
    def test_neither_rejected(self) -> None:
        with pytest.raises(ValidationError, match="exactly one"):
            LookupEntityArgs()
