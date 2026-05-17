"""Tests for ``build_consolidation_strategy`` (ADR-0005 factory)."""

from typing import cast
from unittest.mock import AsyncMock

import pytest

from synthorg.memory.consolidation.abstractive import AbstractiveSummarizer
from synthorg.memory.consolidation.composite import (
    CompositeConsolidationStrategy,
)
from synthorg.memory.consolidation.config import ConsolidationStrategyType
from synthorg.memory.consolidation.density import DensityClassifier
from synthorg.memory.consolidation.extractive import ExtractivePreserver
from synthorg.memory.consolidation.factory import (
    ConsolidationDeps,
    build_consolidation_strategy,
)
from synthorg.memory.consolidation.strategy import ConsolidationStrategy
from synthorg.memory.errors import MemoryConfigError
from synthorg.memory.protocol import MemoryBackend
from synthorg.providers.protocol import CompletionProvider
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def _backend() -> MemoryBackend:
    return cast(
        "MemoryBackend",
        mock_of[MemoryBackend](
            store=AsyncMock(return_value="sum-1"),
            delete=AsyncMock(return_value=True),
        ),
    )


class TestBuildConsolidationStrategy:
    def test_simple_builds_composite(self) -> None:
        strategy = build_consolidation_strategy(
            ConsolidationStrategyType.SIMPLE,
            ConsolidationDeps(backend=_backend()),
        )
        assert isinstance(strategy, CompositeConsolidationStrategy)
        assert isinstance(strategy, ConsolidationStrategy)

    def test_dual_mode_builds_with_deps(self) -> None:
        strategy = build_consolidation_strategy(
            ConsolidationStrategyType.DUAL_MODE,
            ConsolidationDeps(
                backend=_backend(),
                classifier=DensityClassifier(),
                extractor=ExtractivePreserver(),
                summarizer=mock_of[AbstractiveSummarizer](
                    summarize=AsyncMock(return_value="s"),
                ),
            ),
        )
        assert isinstance(strategy, CompositeConsolidationStrategy)

    def test_dual_mode_missing_dep_raises(self) -> None:
        with pytest.raises(MemoryConfigError, match="classifier"):
            build_consolidation_strategy(
                ConsolidationStrategyType.DUAL_MODE,
                ConsolidationDeps(backend=_backend()),
            )

    def test_llm_builds_with_deps(self) -> None:
        strategy = build_consolidation_strategy(
            ConsolidationStrategyType.LLM,
            ConsolidationDeps(
                backend=_backend(),
                provider=mock_of[CompletionProvider](
                    complete=AsyncMock(),
                ),
                model="test-model",
            ),
        )
        assert isinstance(strategy, CompositeConsolidationStrategy)

    def test_llm_missing_provider_raises(self) -> None:
        with pytest.raises(MemoryConfigError, match="provider"):
            build_consolidation_strategy(
                ConsolidationStrategyType.LLM,
                ConsolidationDeps(backend=_backend(), model="m"),
            )

    def test_llm_missing_model_raises(self) -> None:
        with pytest.raises(MemoryConfigError, match="model"):
            build_consolidation_strategy(
                ConsolidationStrategyType.LLM,
                ConsolidationDeps(
                    backend=_backend(),
                    provider=mock_of[CompletionProvider](
                        complete=AsyncMock(),
                    ),
                ),
            )

    def test_group_threshold_threaded_to_selector(self) -> None:
        # group_threshold < 2 must surface the selector's ValueError.
        with pytest.raises(ValueError, match="group_threshold must be >= 2"):
            build_consolidation_strategy(
                ConsolidationStrategyType.SIMPLE,
                ConsolidationDeps(backend=_backend(), group_threshold=1),
            )
