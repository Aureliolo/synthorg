"""Unit tests for the deterministic ``ScriptedDriver``.

The driver is the canonical, config-selectable deterministic
``CompletionProvider`` reused by acceptance tests and the simulation
harness. These tests pin its protocol conformance, the three response
strategies, factory registration, and the safety WARNING.
"""

import pytest
from structlog.testing import capture_logs

from synthorg.config.provider_schema import ProviderConfig
from synthorg.observability.events.provider import (
    PROVIDER_SCRIPTED_DRIVER_INSTANTIATED,
)
from synthorg.providers.drivers.scripted import (
    DeterministicResponseStrategy,
    ScriptedDriver,
    ScriptedProviderExhaustedError,
    ScriptedResponseStrategy,
    SequencedResponseStrategy,
    SingleResponseStrategy,
)
from synthorg.providers.enums import FinishReason, MessageRole
from synthorg.providers.models import ChatMessage, CompletionResponse, TokenUsage
from synthorg.providers.protocol import CompletionProvider
from synthorg.providers.registry import ProviderRegistry

pytestmark = pytest.mark.unit

_MODEL = "scripted-model-001"


def _user(text: str) -> ChatMessage:
    return ChatMessage(role=MessageRole.USER, content=text)


def _text_response(content: str) -> CompletionResponse:
    return CompletionResponse(
        content=content,
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=1, output_tokens=1, cost=0.0),
        model=_MODEL,
    )


class TestProtocolConformance:
    def test_satisfies_completion_provider(self) -> None:
        driver = ScriptedDriver(strategy=DeterministicResponseStrategy())
        assert isinstance(driver, CompletionProvider)

    def test_strategy_protocol_runtime_checkable(self) -> None:
        assert isinstance(DeterministicResponseStrategy(), ScriptedResponseStrategy)
        assert isinstance(
            SequencedResponseStrategy((_text_response("x"),)),
            ScriptedResponseStrategy,
        )

    async def test_capabilities_and_batch(self) -> None:
        driver = ScriptedDriver(strategy=DeterministicResponseStrategy())
        caps = await driver.get_model_capabilities(_MODEL)
        assert caps.model_id == _MODEL
        batch = await driver.batch_get_capabilities((_MODEL, "other"))
        assert set(batch) == {_MODEL, "other"}
        assert batch[_MODEL] is not None


class TestDeterministicStrategy:
    async def test_stable_for_same_input(self) -> None:
        driver = ScriptedDriver(strategy=DeterministicResponseStrategy())
        first = await driver.complete([_user("build the thing")], _MODEL)
        second = await driver.complete([_user("build the thing")], _MODEL)
        assert first.content == second.content
        assert first.finish_reason == FinishReason.STOP
        assert first.content is not None

    async def test_differs_for_different_input(self) -> None:
        driver = ScriptedDriver(strategy=DeterministicResponseStrategy())
        a = await driver.complete([_user("alpha")], _MODEL)
        b = await driver.complete([_user("beta")], _MODEL)
        assert a.content != b.content


class TestSequencedStrategy:
    async def test_plays_in_order(self) -> None:
        r1, r2 = _text_response("one"), _text_response("two")
        driver = ScriptedDriver(strategy=SequencedResponseStrategy((r1, r2)))
        assert (await driver.complete([_user("q")], _MODEL)).content == "one"
        assert (await driver.complete([_user("q")], _MODEL)).content == "two"

    async def test_raises_on_exhaustion(self) -> None:
        driver = ScriptedDriver(
            strategy=SequencedResponseStrategy((_text_response("only"),))
        )
        await driver.complete([_user("q")], _MODEL)
        with pytest.raises(ScriptedProviderExhaustedError):
            await driver.complete([_user("q")], _MODEL)


class TestSingleStrategy:
    async def test_returns_configured_response_repeatedly(self) -> None:
        driver = ScriptedDriver(
            strategy=SingleResponseStrategy(response=_text_response("same"))
        )
        assert (await driver.complete([_user("q")], _MODEL)).content == "same"
        assert (await driver.complete([_user("q")], _MODEL)).content == "same"

    async def test_raises_configured_error(self) -> None:
        boom = ValueError("upstream exploded")
        driver = ScriptedDriver(strategy=SingleResponseStrategy(error=boom))
        with pytest.raises(ValueError, match="upstream exploded"):
            await driver.complete([_user("q")], _MODEL)


class TestFactoryRegistration:
    def test_from_config_builds_scripted_driver(self) -> None:
        registry = ProviderRegistry.from_config(
            {
                "test-provider": ProviderConfig(
                    driver="scripted",
                    models=(),
                ),
            }
        )
        assert isinstance(registry.get("test-provider"), ScriptedDriver)


class TestSafetyWarning:
    def test_warns_on_construction(self) -> None:
        with capture_logs() as logs:
            ScriptedDriver(strategy=DeterministicResponseStrategy())
        assert any(
            entry.get("log_level") == "warning"
            and entry.get("event") == PROVIDER_SCRIPTED_DRIVER_INSTANTIATED
            and entry.get("strategy") == "DeterministicResponseStrategy"
            for entry in logs
        )
