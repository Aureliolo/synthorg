"""Tests for the SafetyClassifier."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.providers.enums import FinishReason
from synthorg.providers.models import (
    CompletionResponse,
    TokenUsage,
    ToolCall,
)
from synthorg.providers.registry import ProviderRegistry
from synthorg.security.config import SafetyClassifierConfig
from synthorg.security.safety_classifier import (
    SafetyClassification,
    SafetyClassifier,
    SafetyClassifierResult,
)

# ── Helpers ───────────────────────────────────────────────────────


def _make_tool_call(
    classification: str = "safe",
    reason: str = "Action appears safe",
) -> ToolCall:
    return ToolCall(
        id="tc-1",
        name="safety_classification_verdict",
        arguments={
            "classification": classification,
            "reason": reason,
        },
    )


def _make_completion(
    tool_call: ToolCall | None = None,
) -> CompletionResponse:
    tc = tool_call or _make_tool_call()
    return CompletionResponse(
        content=None,
        tool_calls=(tc,),
        finish_reason=FinishReason.TOOL_USE,
        usage=TokenUsage(input_tokens=100, output_tokens=30, cost=0.0005),
        model="test-small-001",
    )


def _make_classifier(
    *,
    config: SafetyClassifierConfig | None = None,
    completion: CompletionResponse | None = None,
    driver_map: dict[str, AsyncMock] | None = None,
) -> SafetyClassifier:
    """Build a classifier with mock providers."""
    config_a = MagicMock()
    config_a.family = "family-a"
    config_a.models = (MagicMock(id="model-a-1", alias="small"),)
    config_b = MagicMock()
    config_b.family = "family-b"
    config_b.models = (MagicMock(id="model-b-1", alias="small"),)
    provider_configs = {"provider-a": config_a, "provider-b": config_b}

    if driver_map is None:
        mock_driver = AsyncMock()
        mock_driver.complete = AsyncMock(
            return_value=completion or _make_completion(),
        )
        driver_map = {"provider-a": mock_driver, "provider-b": mock_driver}

    registry = MagicMock(spec=ProviderRegistry)
    registry.get = MagicMock(side_effect=lambda name: driver_map[name])
    registry.list_providers = MagicMock(
        return_value=tuple(sorted(driver_map.keys())),
    )

    return SafetyClassifier(
        provider_registry=registry,
        provider_configs=provider_configs,
        config=config or SafetyClassifierConfig(enabled=True),
    )


# ── Tests: classification results ────────────────────────────────


@pytest.mark.unit
class TestClassificationResults:
    """Classifier returns correct classification for each LLM response."""

    async def test_safe_classification(self) -> None:
        classifier = _make_classifier(
            completion=_make_completion(_make_tool_call("safe", "Looks safe")),
        )
        result = await classifier.classify(
            "Deploy to staging",
            "deploy:staging",
            "deploy-tool",
            ApprovalRiskLevel.MEDIUM,
        )

        assert result.classification == SafetyClassification.SAFE
        assert result.reason == "Looks safe"
        assert result.classification_duration_ms >= 0.0

    async def test_suspicious_classification(self) -> None:
        classifier = _make_classifier(
            completion=_make_completion(
                _make_tool_call("suspicious", "Unusual network call"),
            ),
        )
        result = await classifier.classify(
            "Send data externally",
            "comms:external",
            "http-tool",
            ApprovalRiskLevel.HIGH,
        )

        assert result.classification == SafetyClassification.SUSPICIOUS
        assert result.reason == "Unusual network call"

    async def test_blocked_classification(self) -> None:
        classifier = _make_classifier(
            completion=_make_completion(
                _make_tool_call("blocked", "Credential theft attempt"),
            ),
        )
        result = await classifier.classify(
            "Read /etc/shadow",
            "code:read",
            "file-tool",
            ApprovalRiskLevel.CRITICAL,
        )

        assert result.classification == SafetyClassification.BLOCKED
        assert result.reason == "Credential theft attempt"


# ── Tests: information stripping before LLM ──────────────────────


@pytest.mark.unit
class TestStrippingBeforeLlm:
    """Classifier strips PII before sending to LLM."""

    async def test_stripped_text_sent_to_llm(self) -> None:
        mock_driver = AsyncMock()
        mock_driver.complete = AsyncMock(return_value=_make_completion())
        classifier = _make_classifier(
            driver_map={
                "provider-a": mock_driver,
                "provider-b": mock_driver,
            },
        )

        await classifier.classify(
            "Agent found SSN 123-45-6789 in config",
            "code:read",
            "file-tool",
            ApprovalRiskLevel.LOW,
        )

        # The LLM should NOT see the raw SSN.
        call_args = mock_driver.complete.call_args
        messages = call_args[0][0]
        user_msg = messages[-1].content
        assert "123-45-6789" not in user_msg
        assert "[PII]" in user_msg

    async def test_stripped_description_in_result(self) -> None:
        classifier = _make_classifier()
        result = await classifier.classify(
            "Task task-abc-123 processed user@example.com",
            "code:write",
            "file-tool",
            ApprovalRiskLevel.LOW,
        )

        assert "task-abc-123" not in result.stripped_description
        assert "user@example.com" not in result.stripped_description


# ── Tests: error handling ─────────────────────────────────────────


@pytest.mark.unit
class TestErrorHandling:
    """Errors produce SUSPICIOUS classification (fail-safe)."""

    async def test_provider_failure_returns_suspicious(self) -> None:
        mock_driver = AsyncMock()
        mock_driver.complete = AsyncMock(side_effect=RuntimeError("connection lost"))
        classifier = _make_classifier(
            driver_map={
                "provider-a": mock_driver,
                "provider-b": mock_driver,
            },
        )

        result = await classifier.classify(
            "Some action",
            "code:write",
            "file-tool",
            ApprovalRiskLevel.MEDIUM,
        )

        assert result.classification == SafetyClassification.SUSPICIOUS
        assert "fail-safe" in result.reason.lower() or "failed" in result.reason.lower()

    async def test_timeout_returns_suspicious(self) -> None:
        async def slow_complete(*args: object, **kwargs: object) -> None:
            await asyncio.Event().wait()

        mock_driver = AsyncMock()
        mock_driver.complete = slow_complete
        classifier = _make_classifier(
            config=SafetyClassifierConfig(enabled=True, timeout_seconds=0.01),
            driver_map={
                "provider-a": mock_driver,
                "provider-b": mock_driver,
            },
        )

        result = await classifier.classify(
            "Some action",
            "code:write",
            "file-tool",
            ApprovalRiskLevel.LOW,
        )

        assert result.classification == SafetyClassification.SUSPICIOUS

    async def test_no_providers_returns_suspicious(self) -> None:
        registry = MagicMock(spec=ProviderRegistry)
        registry.list_providers = MagicMock(return_value=())

        config_a = MagicMock()
        config_a.family = "family-a"
        config_a.models = ()
        classifier = SafetyClassifier(
            provider_registry=registry,
            provider_configs={"provider-a": config_a},
            config=SafetyClassifierConfig(enabled=True),
        )

        result = await classifier.classify(
            "Some action",
            "code:write",
            "file-tool",
            ApprovalRiskLevel.LOW,
        )

        assert result.classification == SafetyClassification.SUSPICIOUS
        assert "no provider" in result.reason.lower()

    async def test_invalid_classification_returns_suspicious(self) -> None:
        classifier = _make_classifier(
            completion=_make_completion(
                _make_tool_call("unknown_value", "weird"),
            ),
        )

        result = await classifier.classify(
            "Some action",
            "code:write",
            "file-tool",
            ApprovalRiskLevel.LOW,
        )

        assert result.classification == SafetyClassification.SUSPICIOUS

    async def test_no_tool_call_returns_suspicious(self) -> None:
        response = CompletionResponse(
            content="I think it is safe",
            tool_calls=(),
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(input_tokens=100, output_tokens=30, cost=0.0005),
            model="test-small-001",
        )
        classifier = _make_classifier(completion=response)

        result = await classifier.classify(
            "Some action",
            "code:write",
            "file-tool",
            ApprovalRiskLevel.LOW,
        )

        assert result.classification == SafetyClassification.SUSPICIOUS


# ── Tests: model and config ──────────────────────────────────────


@pytest.mark.unit
class TestConfigAndModel:
    """Config validation and model selection."""

    def test_result_model_frozen(self) -> None:
        result = SafetyClassifierResult(
            classification=SafetyClassification.SAFE,
            stripped_description="test",
            reason="safe",
            classification_duration_ms=1.0,
        )
        with pytest.raises(Exception):  # noqa: B017, PT011
            result.classification = SafetyClassification.BLOCKED  # type: ignore[misc]

    def test_classification_enum_values(self) -> None:
        assert SafetyClassification.SAFE.value == "safe"
        assert SafetyClassification.SUSPICIOUS.value == "suspicious"
        assert SafetyClassification.BLOCKED.value == "blocked"
