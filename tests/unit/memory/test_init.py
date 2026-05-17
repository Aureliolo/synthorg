"""Tests for memory package re-exports."""

import pytest

import synthorg.memory as memory_module


@pytest.mark.unit
class TestMemoryExports:
    def test_all_exports_importable(self) -> None:
        for name in memory_module.__all__:
            assert hasattr(memory_module, name), f"{name} in __all__ but not importable"

    def test_all_has_expected_names(self) -> None:
        expected = {
            "ArchivalMode",
            "ArchivalStore",
            "ConsolidationStrategyType",
            "ContentDensity",
            "EmbedderOverrideConfig",
            "FusionStrategy",
            "Mem0EmbedderConfig",
            "Mem0MemoryBackend",
            "CompanyMemoryConfig",
            "ConsolidationConfig",
            "ConsolidationResult",
            "ConsolidationStrategy",
            "ContextInjectionStrategy",
            "DefaultTokenEstimator",
            "HybridPromptRetrievalBackend",
            "InjectionPoint",
            "InjectionStrategy",
            "MemoryBackend",
            "MemoryCapabilities",
            "MemoryCapabilityError",
            "MemoryConfigError",
            "MemoryConnectionError",
            "MemoryConsolidationService",
            "MemoryEntry",
            "MemoryError",
            "MemoryInjectionStrategy",
            "MemoryMetadata",
            "MemoryNotFoundError",
            "MemoryOptionsConfig",
            "MemoryQuery",
            "MemoryRetrievalConfig",
            "MemoryRetrievalError",
            "MemoryStorageConfig",
            "MemoryStoreError",
            "MemoryStoreRequest",
            "OrgFact",
            "OrgFactAuthor",
            "OrgFactRepository",
            "OrgFactWriteRequest",
            "OrgMemoryBackend",
            "OrgMemoryConfig",
            "OrgMemoryError",
            "OrgMemoryQuery",
            "RetentionEnforcer",
            "ScoredMemory",
            "SharedKnowledgeStore",
            "TokenEstimator",
            "build_consolidation_strategy",
            "create_memory_backend",
            "fuse_ranked_lists",
            "BM25Tokenizer",
            "SparseVector",
            "SelfEditingMemoryConfig",
            "SelfEditingMemoryStrategy",
            "ToolBasedInjectionStrategy",
            "LLMQueryReformulator",
            "LLMSufficiencyChecker",
            "QueryReformulator",
            "SufficiencyChecker",
            "FailureAnalysisPayload",
            "ProceduralMemoryConfig",
            "ProceduralMemoryProposal",
            "ProceduralMemoryProposer",
            "materialize_skill_md",
            "propose_procedural_memory",
        }
        assert set(memory_module.__all__) == expected
