"""Tests for the SafetyClassifier."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pydantic
import pytest

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.core.completion_enums import FinishReason
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
from tests._shared import mock_of
from tests._shared.model_binding import bound_ref, model_ref_resolver

_CLASSIFIER_MODEL = "example-small-001"

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
    bound_pair: str | None = "provider-a",
) -> SafetyClassifier:
    """Build a classifier over mock connections.

    ``bound_pair`` names the connection the operator's
    ``security.safety_classifier_model`` assignment points at; pass ``None``
    to model an unset assignment, where the classifier stays unarmed.
    """
    if driver_map is None:
        mock_driver = AsyncMock()
        mock_driver.complete = AsyncMock(
            return_value=completion or _make_completion(),
        )
        driver_map = {"provider-a": mock_driver, "provider-b": mock_driver}

    names = tuple(sorted(driver_map.keys()))
    registry = mock_of[ProviderRegistry](
        get=MagicMock(side_effect=lambda name: driver_map[name]),
        list_providers=MagicMock(return_value=names),
        __contains__=MagicMock(side_effect=lambda name: name in driver_map),
    )

    return SafetyClassifier(
        provider_registry=registry,
        config=config or SafetyClassifierConfig(enabled=True),
        config_resolver=model_ref_resolver(
            default=bound_ref(_CLASSIFIER_MODEL, provider=bound_pair)
            if bound_pair is not None
            else "",
        ),
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

    async def test_unset_pair_returns_suspicious_without_dispatch(
        self,
    ) -> None:
        mock_driver = AsyncMock()
        mock_driver.complete = AsyncMock(return_value=_make_completion())
        # Connections are registered but the operator has chosen no
        # classification pair, so the classifier must fail safe (SUSPICIOUS)
        # rather than dispatch on whichever connection is to hand.
        classifier = _make_classifier(
            driver_map={"provider-a": mock_driver, "provider-b": mock_driver},
            bound_pair=None,
        )

        result = await classifier.classify(
            "Some action",
            "code:write",
            "file-tool",
            ApprovalRiskLevel.MEDIUM,
        )

        assert result.classification == SafetyClassification.SUSPICIOUS
        mock_driver.complete.assert_not_awaited()

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

    async def test_unregistered_connection_returns_suspicious(self) -> None:
        # The pair names a connection nothing is registered under, which is a
        # misconfiguration: classification cannot run and must not substitute.
        classifier = _make_classifier(bound_pair="ghost-provider")

        result = await classifier.classify(
            "Some action",
            "code:write",
            "file-tool",
            ApprovalRiskLevel.LOW,
        )

        assert result.classification == SafetyClassification.SUSPICIOUS
        assert "no model configured" in result.reason.lower()

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
        with pytest.raises(pydantic.ValidationError):
            result.classification = SafetyClassification.BLOCKED  # type: ignore[misc]

    def test_classification_enum_values(self) -> None:
        assert SafetyClassification.SAFE.value == "safe"
        assert SafetyClassification.SUSPICIOUS.value == "suspicious"
        assert SafetyClassification.BLOCKED.value == "blocked"
