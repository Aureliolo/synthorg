"""Tests for local model management (pull, delete, factory)."""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from synthorg.config.schema import LocalModelParams
from synthorg.providers.management.local_models import (
    _OLLAMA_ERROR_MAX_LEN,
    OllamaModelManager,
    PullProgressEvent,
    _sanitize_ollama_error,
    get_local_model_manager,
)

pytestmark = pytest.mark.unit


class TestPullProgressEvent:
    def test_frozen(self) -> None:
        from pydantic import ValidationError

        evt = PullProgressEvent(status="downloading")
        with pytest.raises(ValidationError, match="frozen"):
            evt.status = "other"  # type: ignore[misc]

    def test_defaults(self) -> None:
        evt = PullProgressEvent(status="pulling")
        assert evt.progress_percent is None
        assert evt.total_bytes is None
        assert evt.completed_bytes is None
        assert evt.error is None
        assert evt.done is False

    def test_error_requires_done(self) -> None:
        """Error events must be terminal (done=True)."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="error events must be terminal"):
            PullProgressEvent(status="failed", error="something broke")

    def test_error_with_done_is_valid(self) -> None:
        evt = PullProgressEvent(
            status="failed",
            error="something broke",
            done=True,
        )
        assert evt.error == "something broke"
        assert evt.done is True

    def test_completed_cannot_exceed_total(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(
            ValidationError,
            match="completed_bytes cannot exceed",
        ):
            PullProgressEvent(
                status="pulling",
                total_bytes=100,
                completed_bytes=200,
            )


class TestGetLocalModelManager:
    def test_ollama_returns_manager(self) -> None:
        manager = get_local_model_manager("ollama", "http://localhost:11434")
        assert isinstance(manager, OllamaModelManager)

    def test_lm_studio_returns_none(self) -> None:
        assert get_local_model_manager("lm-studio", "http://localhost:1234/v1") is None

    def test_vllm_returns_none(self) -> None:
        assert get_local_model_manager("vllm", "http://localhost:8000/v1") is None

    def test_unknown_returns_none(self) -> None:
        result = get_local_model_manager(
            "example-provider",
            "https://api.example.com",
        )
        assert result is None

    def test_none_preset_returns_none(self) -> None:
        assert get_local_model_manager(None, "http://localhost:11434") is None


class TestOllamaModelManagerPull:
    async def test_pull_happy_path(self) -> None:
        """Ollama pull streams newline-delimited JSON and yields events."""
        lines = [
            b'{"status":"pulling manifest"}\n',
            b'{"status":"downloading","total":1000,"completed":500}\n',
            b'{"status":"downloading","total":1000,"completed":1000}\n',
            b'{"status":"success"}\n',
        ]
        stream = AsyncMock()
        stream.aiter_lines = self._make_aiter(
            [line.decode().strip() for line in lines],
        )
        response = AsyncMock(spec=httpx.Response)
        response.status_code = 200
        response.aiter_lines = stream.aiter_lines
        response.aclose = AsyncMock()

        client = AsyncMock(spec=httpx.AsyncClient)
        client.stream = self._make_stream_cm(response)

        manager = OllamaModelManager(
            base_url="http://localhost:11434",
            client=client,
        )
        events = [evt async for evt in manager.pull_model("test-model:latest")]

        assert len(events) == 4
        assert events[0].status == "pulling manifest"
        assert events[1].progress_percent == 50.0
        assert events[2].progress_percent == 100.0
        assert events[3].status == "success"
        assert events[3].done is True

    async def test_pull_error_in_stream(self) -> None:
        """Error response in stream yields event with error field."""
        lines = [
            b'{"status":"pulling manifest"}\n',
            b'{"error":"model not found"}\n',
        ]
        stream = AsyncMock()
        stream.aiter_lines = self._make_aiter(
            [line.decode().strip() for line in lines],
        )
        response = AsyncMock(spec=httpx.Response)
        response.status_code = 200
        response.aiter_lines = stream.aiter_lines
        response.aclose = AsyncMock()

        client = AsyncMock(spec=httpx.AsyncClient)
        client.stream = self._make_stream_cm(response)

        manager = OllamaModelManager(
            base_url="http://localhost:11434",
            client=client,
        )
        events = [evt async for evt in manager.pull_model("nonexistent:latest")]

        assert len(events) == 2
        assert events[1].error == "model not found"
        assert events[1].done is True

    async def test_pull_http_error(self) -> None:
        """Non-200 HTTP response yields a terminal error event."""
        response = AsyncMock(spec=httpx.Response)
        response.status_code = 404
        response.text = "not found"
        response.aclose = AsyncMock()

        client = AsyncMock(spec=httpx.AsyncClient)
        client.stream = self._make_stream_cm(response)

        manager = OllamaModelManager(
            base_url="http://localhost:11434",
            client=client,
        )
        events = [evt async for evt in manager.pull_model("bad-model")]

        assert len(events) == 1
        assert events[0].error is not None
        assert events[0].done is True

    async def test_pull_transport_error(self) -> None:
        """Transport errors (connection refused, timeout) yield terminal event."""
        client = AsyncMock(spec=httpx.AsyncClient)
        client.stream = MagicMock(
            side_effect=httpx.ConnectError("Connection refused"),
        )
        client.aclose = AsyncMock()

        manager = OllamaModelManager(
            base_url="http://localhost:11434",
            client=client,
        )
        events = [evt async for evt in manager.pull_model("test-model")]

        assert len(events) == 1
        assert events[0].error is not None
        assert "Connection refused" in events[0].error
        assert events[0].done is True

    @staticmethod
    def _make_aiter(
        lines: list[str],
    ) -> Callable[[], AsyncIterator[str]]:
        """Create an async iterator from a list of strings."""

        async def _aiter() -> AsyncIterator[str]:
            for line in lines:
                yield line

        return _aiter

    @staticmethod
    def _make_stream_cm(
        response: AsyncMock,
    ) -> Callable[..., AbstractAsyncContextManager[AsyncMock]]:
        """Create a context manager that yields the given response."""
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _stream(
            *_args: object,
            **_kwargs: object,
        ) -> AsyncIterator[AsyncMock]:
            yield response

        return _stream


class TestOllamaModelManagerDelete:
    async def test_delete_success(self) -> None:
        """Successful delete sends correct request."""
        response = MagicMock(spec=httpx.Response)
        response.status_code = 200

        client = AsyncMock(spec=httpx.AsyncClient)
        client.request = AsyncMock(return_value=response)

        manager = OllamaModelManager(
            base_url="http://localhost:11434",
            client=client,
        )
        await manager.delete_model("test-model:latest")

        client.request.assert_awaited_once_with(
            "DELETE",
            "http://localhost:11434/api/delete",
            json={"name": "test-model:latest"},
            timeout=30.0,
        )

    async def test_delete_not_found_raises(self) -> None:
        """404 on delete raises ValueError."""
        response = MagicMock(spec=httpx.Response)
        response.status_code = 404
        response.text = "model not found"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.request = AsyncMock(return_value=response)

        manager = OllamaModelManager(
            base_url="http://localhost:11434",
            client=client,
        )
        with pytest.raises(ValueError, match="not found"):
            await manager.delete_model("nonexistent:latest")


class TestLocalModelParams:
    def test_frozen(self) -> None:
        from pydantic import ValidationError

        params = LocalModelParams(num_ctx=4096)
        with pytest.raises(ValidationError, match="frozen"):
            params.num_ctx = 8192  # type: ignore[misc]

    def test_all_none_by_default(self) -> None:
        params = LocalModelParams()
        assert params.num_ctx is None
        assert params.num_gpu_layers is None
        assert params.num_threads is None
        assert params.num_batch is None
        assert params.repeat_penalty is None

    def test_positive_validation(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            LocalModelParams(num_ctx=0)  # gt=0

    def test_gpu_layers_allows_zero(self) -> None:
        params = LocalModelParams(num_gpu_layers=0)
        assert params.num_gpu_layers == 0

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("num_ctx", float("nan")),
            ("num_gpu_layers", float("inf")),
            ("num_threads", float("-inf")),
            ("repeat_penalty", float("nan")),
            ("repeat_penalty", float("inf")),
        ],
    )
    def test_non_finite_rejected(self, field: str, value: float) -> None:
        """allow_inf_nan=False rejects NaN and Inf."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            LocalModelParams(**{field: value})  # type: ignore[arg-type]


class TestSanitizeOllamaError:
    """Adversarial-input fixtures for the SSE-forwarded error sanitizer."""

    def test_non_string_falls_back(self) -> None:
        assert _sanitize_ollama_error(None) == "Pull failed"
        assert _sanitize_ollama_error(12345) == "Pull failed"
        assert _sanitize_ollama_error({"error": "x"}) == "Pull failed"

    def test_redacts_posix_paths(self) -> None:
        sanitized = _sanitize_ollama_error(
            "open /var/lib/ollama/models/secret.bin: permission denied",
        )
        assert "/var/lib/ollama" not in sanitized
        assert "[REDACTED-PATH]" in sanitized

    def test_redacts_posix_paths_with_spaces(self) -> None:
        # Path segments that contain a literal space must still be
        # consumed in full -- the older regex stopped at the first
        # whitespace and leaked the trailing tail.
        sanitized = _sanitize_ollama_error(
            "open /var/lib/ollama/model cache/secret.bin: permission denied",
        )
        assert "model cache" not in sanitized
        assert "secret.bin" not in sanitized
        assert "[REDACTED-PATH]" in sanitized

    def test_redacts_windows_paths(self) -> None:
        sanitized = _sanitize_ollama_error(
            r"C:\Users\admin\AppData\token.json missing",
        )
        assert "Users" not in sanitized
        assert "[REDACTED-PATH]" in sanitized

    def test_redacts_windows_paths_with_spaces(self) -> None:
        # ``Program Files`` is the canonical Windows-path-with-space
        # case; the redaction must consume both segments.
        sanitized = _sanitize_ollama_error(
            r"C:\Program Files\Ollama\token.json missing",
        )
        assert "Program Files" not in sanitized
        assert "token.json" not in sanitized
        assert "[REDACTED-PATH]" in sanitized

    def test_redacts_host_port(self) -> None:
        sanitized = _sanitize_ollama_error(
            "dial tcp ollama-internal.local:11434: connection refused",
        )
        assert "ollama-internal.local" not in sanitized
        assert "[REDACTED-HOST]" in sanitized

    def test_truncates_oversized_input(self) -> None:
        long = "x" * 1000
        sanitized = _sanitize_ollama_error(long)
        assert len(sanitized) <= _OLLAMA_ERROR_MAX_LEN

    def test_benign_message_passes_through(self) -> None:
        sanitized = _sanitize_ollama_error("model not found")
        assert sanitized == "model not found"

    def test_empty_string_falls_back(self) -> None:
        assert _sanitize_ollama_error("") == "Pull failed"
        assert _sanitize_ollama_error("   ") == "Pull failed"
