"""Unit tests for LiteLLMDriver.

All tests mock ``litellm.acompletion`` — no real API calls are made.
"""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from synthorg.config.schema import ProviderConfig, ProviderModelConfig
from synthorg.providers.drivers.litellm_driver import LiteLLMDriver
from synthorg.providers.enums import (
    FinishReason,
    MessageRole,
    StreamEventType,
)
from synthorg.providers.errors import (
    AuthenticationError,
    ContentFilterError,
    InvalidRequestError,
    ModelNotFoundError,
    ProviderConnectionError,
    ProviderError,
    ProviderInternalError,
    ProviderTimeoutError,
    RateLimitError,
)
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    StreamChunk,
    ToolDefinition,
)

from .conftest import (
    make_mock_response,
    make_mock_tool_call,
    make_provider_config,
    make_stream_chunk,
    make_stream_tool_call_delta,
    mock_stream_response,
)

# ── Helpers ──────────────────────────────────────────────────────

_PATCH_ACOMPLETION = "synthorg.providers.drivers.litellm_driver._litellm.acompletion"
_PATCH_MODEL_INFO = "synthorg.providers.drivers.litellm_driver._litellm.get_model_info"


def _make_driver(
    provider_name: str = "example-provider",
    config: ProviderConfig | None = None,
) -> LiteLLMDriver:
    return LiteLLMDriver(
        provider_name,
        config or make_provider_config(),
    )


def _user_message(
    content: str = "Hello",
) -> list[ChatMessage]:
    return [ChatMessage(role=MessageRole.USER, content=content)]


async def _collect_stream(
    driver: LiteLLMDriver,
    mock_call: AsyncMock,
    chunks: list[MagicMock],
    model: str = "medium",
) -> list[StreamChunk]:
    mock_call.return_value = mock_stream_response(chunks)
    stream = await driver.stream(_user_message(), model)
    return [chunk async for chunk in stream]


# ── Non-streaming completion ─────────────────────────────────────


@pytest.mark.unit
class TestDoComplete:
    async def test_basic_completion(self) -> None:
        driver = _make_driver()
        mock_resp = make_mock_response()

        with patch(_PATCH_ACOMPLETION, new_callable=AsyncMock) as m:
            m.return_value = mock_resp
            result = await driver.complete(_user_message(), "medium")

        assert result.content == "Hello! How can I help?"
        assert result.finish_reason == FinishReason.STOP
        assert result.model == "test-model-001"
        assert result.usage.input_tokens == 100
        assert result.usage.output_tokens == 50

    async def test_completion_with_tool_calls(self) -> None:
        driver = _make_driver()
        tc = make_mock_tool_call()
        mock_resp = make_mock_response(
            content=None,
            tool_calls=[tc],
            finish_reason="tool_calls",
        )

        with patch(_PATCH_ACOMPLETION, new_callable=AsyncMock) as m:
            m.return_value = mock_resp
            result = await driver.complete(_user_message(), "medium")

        assert result.content is None
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].id == "call_001"
        assert result.tool_calls[0].name == "get_weather"
        assert result.finish_reason == FinishReason.TOOL_USE

    async def test_model_alias_resolution(self) -> None:
        driver = _make_driver()
        mock_resp = make_mock_response()

        with patch(_PATCH_ACOMPLETION, new_callable=AsyncMock) as m:
            m.return_value = mock_resp
            await driver.complete(_user_message(), "small")

        kw = m.call_args.kwargs
        assert kw["model"] == "example-provider/test-model-002"

    async def test_model_id_resolution(self) -> None:
        driver = _make_driver()
        mock_resp = make_mock_response()

        with patch(_PATCH_ACOMPLETION, new_callable=AsyncMock) as m:
            m.return_value = mock_resp
            await driver.complete(
                _user_message(),
                "test-model-001",
            )

        kw = m.call_args.kwargs
        assert kw["model"] == "example-provider/test-model-001"

    async def test_unknown_model_raises(self) -> None:
        driver = _make_driver()

        with pytest.raises(ModelNotFoundError, match="nonexistent"):
            await driver.complete(_user_message(), "nonexistent")

    async def test_api_key_passed_to_litellm(self) -> None:
        driver = _make_driver()
        mock_resp = make_mock_response()

        with patch(_PATCH_ACOMPLETION, new_callable=AsyncMock) as m:
            m.return_value = mock_resp
            await driver.complete(_user_message(), "medium")

        assert m.call_args.kwargs["api_key"] == "sk-test-key"

    async def test_base_url_passed_to_litellm(self) -> None:
        config = make_provider_config(
            base_url="https://custom.api.example.com",
        )
        driver = _make_driver(config=config)
        mock_resp = make_mock_response()

        with patch(_PATCH_ACOMPLETION, new_callable=AsyncMock) as m:
            m.return_value = mock_resp
            await driver.complete(_user_message(), "medium")

        kw = m.call_args.kwargs
        assert kw["api_base"] == "https://custom.api.example.com"

    async def test_completion_config_parameters(self) -> None:
        driver = _make_driver()
        mock_resp = make_mock_response()
        comp_config = CompletionConfig(
            temperature=0.5,
            max_tokens=1024,
            stop_sequences=("END",),
            top_p=0.9,
            timeout=30.0,
        )

        with patch(_PATCH_ACOMPLETION, new_callable=AsyncMock) as m:
            m.return_value = mock_resp
            await driver.complete(
                _user_message(),
                "medium",
                config=comp_config,
            )

        kw = m.call_args.kwargs
        assert kw["temperature"] == 0.5
        assert kw["max_tokens"] == 1024
        assert kw["stop"] == ["END"]
        assert kw["top_p"] == 0.9
        assert kw["timeout"] == 30.0

    async def test_tools_passed_to_litellm(self) -> None:
        driver = _make_driver()
        mock_resp = make_mock_response()
        tools = [
            ToolDefinition(
                name="search",
                description="Search code",
                parameters_schema={"type": "object"},
            ),
        ]

        with patch(_PATCH_ACOMPLETION, new_callable=AsyncMock) as m:
            m.return_value = mock_resp
            await driver.complete(
                _user_message(),
                "medium",
                tools=tools,
            )

        kw = m.call_args.kwargs
        assert "tools" in kw
        assert kw["tools"][0]["function"]["name"] == "search"

    async def test_provider_request_id_captured(self) -> None:
        driver = _make_driver()
        mock_resp = make_mock_response(request_id="req_xyz789")

        with patch(_PATCH_ACOMPLETION, new_callable=AsyncMock) as m:
            m.return_value = mock_resp
            result = await driver.complete(_user_message(), "medium")

        assert result.provider_request_id == "req_xyz789"

    async def test_cost_computed_from_config(self) -> None:
        driver = _make_driver()
        mock_resp = make_mock_response(
            prompt_tokens=1000,
            completion_tokens=500,
        )

        with patch(_PATCH_ACOMPLETION, new_callable=AsyncMock) as m:
            m.return_value = mock_resp
            result = await driver.complete(_user_message(), "medium")

        # medium: 0.003/1k in + 0.015/1k out = 0.0105
        assert result.usage.cost_usd == 0.0105


# ── Streaming ────────────────────────────────────────────────────


@pytest.mark.unit
class TestDoStream:
    async def test_basic_streaming(self) -> None:
        driver = _make_driver()
        chunks = [
            make_stream_chunk(content="Hello"),
            make_stream_chunk(content=" world"),
            make_stream_chunk(
                finish_reason="stop",
                prompt_tokens=100,
                completion_tokens=50,
            ),
        ]

        with patch(_PATCH_ACOMPLETION, new_callable=AsyncMock) as m:
            collected = await _collect_stream(driver, m, chunks)

        content_chunks = [
            c for c in collected if c.event_type == StreamEventType.CONTENT_DELTA
        ]
        assert len(content_chunks) == 2
        assert content_chunks[0].content == "Hello"
        assert content_chunks[1].content == " world"
        assert collected[-1].event_type == StreamEventType.DONE

    async def test_streaming_with_tool_calls(self) -> None:
        driver = _make_driver()
        td1 = make_stream_tool_call_delta(
            index=0,
            call_id="call_001",
            name="search",
            arguments='{"qu',
        )
        td2 = make_stream_tool_call_delta(
            index=0,
            arguments='ery": "test"}',
        )
        chunks = [
            make_stream_chunk(tool_calls=[td1]),
            make_stream_chunk(tool_calls=[td2]),
            make_stream_chunk(finish_reason="tool_calls"),
        ]

        with patch(_PATCH_ACOMPLETION, new_callable=AsyncMock) as m:
            collected = await _collect_stream(driver, m, chunks)

        tc_chunks = [
            c for c in collected if c.event_type == StreamEventType.TOOL_CALL_DELTA
        ]
        assert len(tc_chunks) == 1
        tc = tc_chunks[0].tool_call_delta
        assert tc is not None
        assert tc.id == "call_001"
        assert tc.name == "search"
        assert tc.arguments == {"query": "test"}

    async def test_streaming_usage_chunk(self) -> None:
        driver = _make_driver()
        chunks = [
            make_stream_chunk(content="Hi"),
            make_stream_chunk(
                finish_reason="stop",
                prompt_tokens=50,
                completion_tokens=10,
            ),
        ]

        with patch(_PATCH_ACOMPLETION, new_callable=AsyncMock) as m:
            collected = await _collect_stream(driver, m, chunks)

        usage_chunks = [c for c in collected if c.event_type == StreamEventType.USAGE]
        assert len(usage_chunks) == 1
        assert usage_chunks[0].usage is not None
        assert usage_chunks[0].usage.input_tokens == 50
        assert usage_chunks[0].usage.output_tokens == 10

    async def test_stream_sets_stream_option(self) -> None:
        driver = _make_driver()
        chunks = [make_stream_chunk(content="ok")]

        with patch(_PATCH_ACOMPLETION, new_callable=AsyncMock) as m:
            await _collect_stream(driver, m, chunks)

        kw = m.call_args.kwargs
        assert kw["stream"] is True
        assert kw["stream_options"] == {"include_usage": True}

    async def test_streaming_incomplete_tool_call_dropped(self) -> None:
        """Tool call with no id/name is silently dropped."""
        driver = _make_driver()
        # Delta with arguments but no id or name
        td = make_stream_tool_call_delta(
            index=0,
            arguments='{"query": "test"}',
        )
        chunks = [
            make_stream_chunk(tool_calls=[td]),
            make_stream_chunk(finish_reason="tool_calls"),
        ]

        with patch(_PATCH_ACOMPLETION, new_callable=AsyncMock) as m:
            collected = await _collect_stream(driver, m, chunks)

        tc_chunks = [
            c for c in collected if c.event_type == StreamEventType.TOOL_CALL_DELTA
        ]
        assert len(tc_chunks) == 0

    async def test_streaming_multiple_concurrent_tool_calls(self) -> None:
        """Multiple tool calls at different indices are emitted separately."""
        driver = _make_driver()
        td1_a = make_stream_tool_call_delta(
            index=0,
            call_id="call_001",
            name="search",
            arguments='{"q":',
        )
        td2_a = make_stream_tool_call_delta(
            index=1,
            call_id="call_002",
            name="read",
            arguments='{"path":',
        )
        td1_b = make_stream_tool_call_delta(
            index=0,
            arguments=' "test"}',
        )
        td2_b = make_stream_tool_call_delta(
            index=1,
            arguments=' "f.py"}',
        )
        chunks = [
            make_stream_chunk(tool_calls=[td1_a, td2_a]),
            make_stream_chunk(tool_calls=[td1_b, td2_b]),
            make_stream_chunk(finish_reason="tool_calls"),
        ]

        with patch(_PATCH_ACOMPLETION, new_callable=AsyncMock) as m:
            collected = await _collect_stream(driver, m, chunks)

        tc_chunks = [
            c for c in collected if c.event_type == StreamEventType.TOOL_CALL_DELTA
        ]
        assert len(tc_chunks) == 2
        tc0 = tc_chunks[0].tool_call_delta
        tc1 = tc_chunks[1].tool_call_delta
        assert tc0 is not None
        assert tc1 is not None
        assert tc0.id == "call_001"
        assert tc0.name == "search"
        assert tc0.arguments == {"q": "test"}
        assert tc1.id == "call_002"
        assert tc1.name == "read"
        assert tc1.arguments == {"path": "f.py"}

    async def test_streaming_usage_only_chunk_no_choices(self) -> None:
        """Usage-only chunk with empty choices is emitted."""
        from unittest.mock import MagicMock

        driver = _make_driver()
        # Usage-only chunk: choices=[] with usage present
        usage_chunk = MagicMock()
        usage_chunk.choices = []
        usage_obj = MagicMock()
        usage_obj.prompt_tokens = 50
        usage_obj.completion_tokens = 10
        usage_obj.total_tokens = 60
        usage_chunk.usage = usage_obj

        content_chunk = make_stream_chunk(content="Hi")
        chunks = [content_chunk, usage_chunk]

        with patch(_PATCH_ACOMPLETION, new_callable=AsyncMock) as m:
            collected = await _collect_stream(driver, m, chunks)

        usage_chunks = [c for c in collected if c.event_type == StreamEventType.USAGE]
        assert len(usage_chunks) == 1
        assert usage_chunks[0].usage is not None
        assert usage_chunks[0].usage.input_tokens == 50

    async def test_streaming_usage_emitted_when_prompt_tokens_zero(self) -> None:
        """Usage with prompt_tokens=0 is still emitted."""
        driver = _make_driver()
        chunks = [
            make_stream_chunk(
                content="Hi",
                prompt_tokens=0,
                completion_tokens=10,
            ),
        ]

        with patch(_PATCH_ACOMPLETION, new_callable=AsyncMock) as m:
            collected = await _collect_stream(driver, m, chunks)

        usage_chunks = [c for c in collected if c.event_type == StreamEventType.USAGE]
        assert len(usage_chunks) == 1
        assert usage_chunks[0].usage is not None
        assert usage_chunks[0].usage.input_tokens == 0
        assert usage_chunks[0].usage.output_tokens == 10

    async def test_tool_call_arguments_length_limit(self) -> None:
        """Tool call arguments exceeding 1 MiB are truncated."""
        from synthorg.providers.drivers.litellm_driver import _ToolCallAccumulator

        acc = _ToolCallAccumulator()
        acc.id = "call_001"
        acc.name = "test_tool"

        # Fill up to near the limit
        large_fragment = "x" * (acc._MAX_ARGUMENTS_LEN - 10)
        acc.arguments = large_fragment

        # This should be rejected (would exceed limit)
        from unittest.mock import MagicMock

        delta = MagicMock()
        delta.id = None
        func = MagicMock()
        func.name = None
        func.arguments = "y" * 100
        delta.function = func
        acc.update(delta)

        # Arguments should not have grown
        assert len(acc.arguments) == len(large_fragment)


# ── Exception mapping ────────────────────────────────────────────


@pytest.mark.unit
class TestExceptionMapping:
    @pytest.mark.parametrize(
        ("litellm_exc_name", "expected_type"),
        [
            ("AuthenticationError", AuthenticationError),
            ("RateLimitError", RateLimitError),
            ("NotFoundError", ModelNotFoundError),
            ("ContextWindowExceededError", InvalidRequestError),
            ("ContentPolicyViolationError", ContentFilterError),
            ("BadRequestError", InvalidRequestError),
            ("Timeout", ProviderTimeoutError),
            ("ServiceUnavailableError", ProviderInternalError),
            ("InternalServerError", ProviderInternalError),
            ("APIConnectionError", ProviderConnectionError),
        ],
    )
    async def test_exception_mapping(
        self,
        litellm_exc_name: str,
        expected_type: type,
    ) -> None:
        import litellm as _litellm

        driver = _make_driver()
        exc_class = getattr(_litellm, litellm_exc_name)
        kwargs = _litellm_exc_kwargs(litellm_exc_name)
        litellm_exc = exc_class(**kwargs)

        with patch(
            _PATCH_ACOMPLETION,
            new_callable=AsyncMock,
        ) as m:
            m.side_effect = litellm_exc
            with pytest.raises(expected_type) as exc_info:
                await driver.complete(_user_message(), "medium")

        assert isinstance(exc_info.value, ProviderError)
        assert exc_info.value.context["provider"] == "example-provider"

    async def test_rate_limit_retry_after_extracted(self) -> None:
        import litellm as _litellm

        driver = _make_driver()
        exc = _litellm.RateLimitError(  # type: ignore[attr-defined]
            message="Rate limited",
            model="test",
            llm_provider="example-provider",
        )
        exc.headers = {"retry-after": "30"}  # type: ignore[attr-defined]

        with patch(
            _PATCH_ACOMPLETION,
            new_callable=AsyncMock,
        ) as m:
            m.side_effect = exc
            with pytest.raises(RateLimitError) as exc_info:
                await driver.complete(_user_message(), "medium")

        assert exc_info.value.retry_after == 30.0

    async def test_rate_limit_retry_after_case_insensitive(self) -> None:
        """Header lookup is case-insensitive per HTTP semantics."""
        import litellm as _litellm

        driver = _make_driver()
        exc = _litellm.RateLimitError(  # type: ignore[attr-defined]
            message="Rate limited",
            model="test",
            llm_provider="example-provider",
        )
        exc.headers = {"Retry-After": "15"}  # type: ignore[attr-defined]

        with patch(
            _PATCH_ACOMPLETION,
            new_callable=AsyncMock,
        ) as m:
            m.side_effect = exc
            with pytest.raises(RateLimitError) as exc_info:
                await driver.complete(_user_message(), "medium")

        assert exc_info.value.retry_after == 15.0

    async def test_rate_limit_no_headers(self) -> None:
        """No headers attribute yields retry_after=None."""
        import litellm as _litellm

        driver = _make_driver()
        exc = _litellm.RateLimitError(  # type: ignore[attr-defined]
            message="Rate limited",
            model="test",
            llm_provider="example-provider",
        )

        with patch(
            _PATCH_ACOMPLETION,
            new_callable=AsyncMock,
        ) as m:
            m.side_effect = exc
            with pytest.raises(RateLimitError) as exc_info:
                await driver.complete(_user_message(), "medium")

        assert exc_info.value.retry_after is None

    async def test_rate_limit_non_numeric_retry_after(self) -> None:
        """Non-numeric retry-after gracefully returns None."""
        import litellm as _litellm

        driver = _make_driver()
        exc = _litellm.RateLimitError(  # type: ignore[attr-defined]
            message="Rate limited",
            model="test",
            llm_provider="example-provider",
        )
        exc.headers = {  # type: ignore[attr-defined]
            "retry-after": "Wed, 21 Oct 2025 07:28:00 GMT",
        }

        with patch(
            _PATCH_ACOMPLETION,
            new_callable=AsyncMock,
        ) as m:
            m.side_effect = exc
            with pytest.raises(RateLimitError) as exc_info:
                await driver.complete(_user_message(), "medium")

        assert exc_info.value.retry_after is None

    async def test_unknown_exception_maps_to_internal(self) -> None:
        driver = _make_driver()

        with patch(
            _PATCH_ACOMPLETION,
            new_callable=AsyncMock,
        ) as m:
            m.side_effect = RuntimeError("something broke")
            with pytest.raises(
                ProviderInternalError,
                match="Unexpected",
            ):
                await driver.complete(
                    _user_message(),
                    "medium",
                )

    async def test_stream_exception_during_iteration(self) -> None:
        import litellm as _litellm

        driver = _make_driver()

        async def _failing_stream() -> AsyncIterator[MagicMock]:
            yield make_stream_chunk(content="Hi")
            raise _litellm.Timeout(  # type: ignore[attr-defined]
                message="Stream timed out",
                model="test",
                llm_provider="example-provider",
            )

        with patch(
            _PATCH_ACOMPLETION,
            new_callable=AsyncMock,
        ) as m:
            m.return_value = _failing_stream()
            stream = await driver.stream(
                _user_message(),
                "medium",
            )
            with pytest.raises(ProviderTimeoutError):
                async for _ in stream:
                    pass

    async def test_stream_exception_before_iteration(self) -> None:
        """Stream setup failure maps to ProviderError."""
        import litellm as _litellm

        driver = _make_driver()
        with patch(
            _PATCH_ACOMPLETION,
            new_callable=AsyncMock,
        ) as m:
            m.side_effect = _litellm.AuthenticationError(  # type: ignore[attr-defined]
                message="Invalid key",
                model="test",
                llm_provider="example-provider",
            )
            with pytest.raises(AuthenticationError):
                await driver.stream(_user_message(), "medium")

    async def test_response_mapping_error_wrapped_as_provider_error(self) -> None:
        """Errors during response mapping are caught, not leaked raw."""
        from unittest.mock import MagicMock

        driver = _make_driver()
        response = MagicMock()
        response.choices = []  # empty choices triggers our guard

        with patch(
            _PATCH_ACOMPLETION,
            new_callable=AsyncMock,
        ) as m:
            m.return_value = response
            with pytest.raises(ProviderError):
                await driver.complete(_user_message(), "medium")


# ── Model capabilities ───────────────────────────────────────────


@pytest.mark.unit
class TestGetModelCapabilities:
    async def test_basic_capabilities(self) -> None:
        driver = _make_driver()
        model_info = {
            "max_output_tokens": 8192,
            "supports_function_calling": True,
            "supports_vision": True,
            "supports_system_messages": True,
        }

        with patch(
            _PATCH_MODEL_INFO,
            return_value=model_info,
        ):
            caps = await driver.get_model_capabilities("medium")

        assert caps.model_id == "test-model-001"
        assert caps.provider == "example-provider"
        assert caps.max_context_tokens == 200_000
        assert caps.max_output_tokens == 8192
        assert caps.supports_tools is True
        assert caps.supports_vision is True
        assert caps.cost_per_1k_input == 0.003
        assert caps.cost_per_1k_output == 0.015

    async def test_capabilities_fallback_on_litellm_error(self) -> None:
        driver = _make_driver()

        with patch(
            _PATCH_MODEL_INFO,
            side_effect=Exception("Unknown model"),
        ):
            caps = await driver.get_model_capabilities("medium")

        assert caps.model_id == "test-model-001"
        assert caps.max_output_tokens == 4096

    async def test_streaming_capability_from_model_info(self) -> None:
        """supports_streaming reads from model info, not hard-coded."""
        driver = _make_driver()
        model_info = {
            "supports_streaming": False,
            "supports_function_calling": True,
        }

        with patch(
            _PATCH_MODEL_INFO,
            return_value=model_info,
        ):
            caps = await driver.get_model_capabilities("medium")

        assert caps.supports_streaming is False
        assert caps.supports_streaming_tool_calls is False

    async def test_streaming_tool_calls_requires_both(self) -> None:
        """supports_streaming_tool_calls needs streaming AND tools."""
        driver = _make_driver()
        model_info = {
            "supports_streaming": True,
            "supports_function_calling": False,
        }

        with patch(
            _PATCH_MODEL_INFO,
            return_value=model_info,
        ):
            caps = await driver.get_model_capabilities("medium")

        assert caps.supports_streaming is True
        assert caps.supports_streaming_tool_calls is False

    async def test_max_output_capped_at_context(self) -> None:
        config = make_provider_config(
            models=(
                ProviderModelConfig(
                    id="small-model",
                    max_context=1024,
                    cost_per_1k_input=0.001,
                    cost_per_1k_output=0.002,
                ),
            ),
        )
        driver = _make_driver(config=config)
        model_info = {"max_output_tokens": 999_999}

        with patch(
            _PATCH_MODEL_INFO,
            return_value=model_info,
        ):
            caps = await driver.get_model_capabilities(
                "small-model",
            )

        assert caps.max_output_tokens == 1024


# ── Helpers ──────────────────────────────────────────────────────


def _litellm_exc_kwargs(exc_name: str) -> dict[str, str]:
    """Build constructor kwargs for litellm exceptions."""
    return {
        "message": f"Test {exc_name}",
        "model": "test-model",
        "llm_provider": "test",
    }
