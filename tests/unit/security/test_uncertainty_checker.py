"""Tests for the UncertaintyChecker."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.core.completion_enums import FinishReason
from synthorg.providers.models import (
    CompletionResponse,
    TokenUsage,
)
from synthorg.providers.registry import ProviderRegistry
from synthorg.providers.routing.resolver import ModelResolver
from synthorg.security.config import UncertaintyCheckConfig
from synthorg.security.uncertainty import (
    UncertaintyChecker,
    UncertaintyResult,
    _compute_keyword_overlap,
    _compute_tfidf_cosine_similarity,
)

# ── Helpers ───────────────────────────────────────────────────────


def _make_response(content: str) -> CompletionResponse:
    return CompletionResponse(
        content=content,
        tool_calls=(),
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=50, output_tokens=20, cost=0.0002),
        model="test-small-001",
    )


def _make_checker(
    *,
    config: UncertaintyCheckConfig | None = None,
    responses: list[CompletionResponse] | None = None,
    provider_count: int = 2,
) -> UncertaintyChecker:
    """Build a checker with mock providers and resolver."""
    if responses is None:
        responses = [_make_response("The answer is 42")] * provider_count

    drivers: dict[str, AsyncMock] = {}
    resolved_models: list[MagicMock] = []
    for i in range(provider_count):
        name = f"provider-{chr(97 + i)}"
        driver = AsyncMock()
        driver.complete = AsyncMock(
            return_value=responses[i] if i < len(responses) else responses[-1],
        )
        drivers[name] = driver
        model = MagicMock()
        model.provider_name = name
        model.model_id = f"model-{chr(97 + i)}-001"
        resolved_models.append(model)

    registry = MagicMock(spec=ProviderRegistry)
    registry.get = MagicMock(side_effect=lambda name: drivers[name])

    resolver = MagicMock(spec=ModelResolver)
    resolver.resolve_all = MagicMock(return_value=tuple(resolved_models))

    return UncertaintyChecker(
        provider_registry=registry,
        model_resolver=resolver,
        config=config
        or UncertaintyCheckConfig(
            enabled=True,
            model_ref="small",
        ),
    )


# ── Tests: similarity functions ──────────────────────────────────


@pytest.mark.unit
class TestKeywordOverlap:
    """Keyword overlap (Jaccard) computation."""

    def test_identical_texts(self) -> None:
        result = _compute_keyword_overlap(["hello world", "hello world"])
        assert result == pytest.approx(1.0)

    def test_completely_different(self) -> None:
        result = _compute_keyword_overlap(["cat dog", "fish bird"])
        assert result == pytest.approx(0.0)

    def test_partial_overlap(self) -> None:
        result = _compute_keyword_overlap(["hello world foo", "hello bar baz"])
        # Jaccard: |{hello}| / |{hello, world, foo, bar, baz}| = 1/5
        assert result == pytest.approx(0.2)

    def test_single_response(self) -> None:
        result = _compute_keyword_overlap(["only one"])
        assert result == pytest.approx(1.0)

    def test_empty_responses(self) -> None:
        result = _compute_keyword_overlap(["", ""])
        assert result == pytest.approx(1.0)


@pytest.mark.unit
class TestTfidfCosineSimilarity:
    """TF-IDF cosine similarity computation."""

    def test_identical_texts(self) -> None:
        result = _compute_tfidf_cosine_similarity(
            ["the quick brown fox", "the quick brown fox"],
        )
        assert result == pytest.approx(1.0, abs=0.01)

    def test_completely_different(self) -> None:
        result = _compute_tfidf_cosine_similarity(
            ["cat dog hamster", "python java rust"],
        )
        assert result == pytest.approx(0.0)

    def test_partial_similarity(self) -> None:
        result = _compute_tfidf_cosine_similarity(
            ["the quick brown fox jumps", "the slow brown dog sits"],
        )
        # Some overlap (the, brown) but not identical
        assert 0.0 < result < 1.0

    def test_single_response(self) -> None:
        result = _compute_tfidf_cosine_similarity(["only one"])
        assert result == pytest.approx(1.0)


# ── Tests: checker behavior ──────────────────────────────────────


@pytest.mark.unit
class TestHighConfidence:
    """Identical responses produce high confidence."""

    async def test_identical_responses(self) -> None:
        checker = _make_checker(
            responses=[
                _make_response("The answer is 42"),
                _make_response("The answer is 42"),
            ],
        )
        result = await checker.check("What is the answer?")

        assert result.confidence_score >= 0.9
        assert result.provider_count == 2

    async def test_very_similar_responses(self) -> None:
        checker = _make_checker(
            responses=[
                _make_response("The answer is 42."),
                _make_response("The answer is 42"),
            ],
        )
        result = await checker.check("What is the answer?")

        assert result.confidence_score >= 0.8


@pytest.mark.unit
class TestLowConfidence:
    """Divergent responses produce low confidence."""

    async def test_divergent_responses(self) -> None:
        checker = _make_checker(
            responses=[
                _make_response("Cats are independent and aloof animals"),
                _make_response("Python is a programming language for data"),
            ],
        )
        result = await checker.check("Tell me something")

        assert result.confidence_score < 0.5
        assert result.provider_count == 2


@pytest.mark.unit
class TestSkipConditions:
    """Check is skipped when insufficient providers available."""

    async def test_single_provider_skips(self) -> None:
        checker = _make_checker(provider_count=1)
        result = await checker.check("What is the answer?")

        assert result.confidence_score == 1.0
        assert result.provider_count == 0
        assert (
            "insufficient" in result.reason.lower() or "skip" in result.reason.lower()
        )

    async def test_no_model_ref_skips(self) -> None:
        checker = _make_checker(
            config=UncertaintyCheckConfig(enabled=True, model_ref=None),
        )
        result = await checker.check("What is the answer?")

        assert result.confidence_score == 1.0
        assert result.provider_count == 0


# ── Tests: error handling ─────────────────────────────────────────


@pytest.mark.unit
class TestCheckerErrors:
    """Provider failures degrade gracefully."""

    async def test_one_provider_fails_returns_high_confidence(self) -> None:
        """When one of two providers fails, only one response remains."""
        good_driver = AsyncMock()
        good_driver.complete = AsyncMock(
            return_value=_make_response("The answer is 42"),
        )
        bad_driver = AsyncMock()
        bad_driver.complete = AsyncMock(
            side_effect=RuntimeError("connection lost"),
        )

        resolver = MagicMock(spec=ModelResolver)
        model_a = MagicMock()
        model_a.provider_name = "provider-a"
        model_a.model_id = "model-a-001"
        model_b = MagicMock()
        model_b.provider_name = "provider-b"
        model_b.model_id = "model-b-001"
        resolver.resolve_all = MagicMock(return_value=(model_a, model_b))

        registry = MagicMock(spec=ProviderRegistry)
        registry.get = MagicMock(
            side_effect=lambda name: (
                good_driver if name == "provider-a" else bad_driver
            ),
        )

        checker = UncertaintyChecker(
            provider_registry=registry,
            model_resolver=resolver,
            config=UncertaintyCheckConfig(
                enabled=True,
                model_ref="small",
            ),
        )

        result = await checker.check("What is the answer?")

        # Only 1 response -- insufficient for comparison
        assert result.confidence_score == 1.0


# ── Tests: result model ──────────────────────────────────────────


@pytest.mark.unit
class TestResultModel:
    """UncertaintyResult model validation."""

    def test_frozen(self) -> None:
        result = UncertaintyResult(
            confidence_score=0.8,
            provider_count=2,
            reason="Cross-provider check complete",
            check_duration_ms=50.0,
        )
        with pytest.raises(Exception):  # noqa: B017, PT011
            result.confidence_score = 0.5  # type: ignore[misc]

    def test_score_bounds(self) -> None:
        with pytest.raises(Exception):  # noqa: B017, PT011
            UncertaintyResult(
                confidence_score=1.5,
                provider_count=2,
                reason="bad",
                check_duration_ms=0.0,
            )

    def test_negative_score_rejected(self) -> None:
        with pytest.raises(Exception):  # noqa: B017, PT011
            UncertaintyResult(
                confidence_score=-0.1,
                provider_count=2,
                reason="bad",
                check_duration_ms=0.0,
            )
