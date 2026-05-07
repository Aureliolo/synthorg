"""Local model management for local LLM providers.

Provides a ``LocalModelManager`` protocol for pull/delete operations
on local LLM providers, plus a concrete implementation for Ollama.
"""

import json
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.normalization import strip_trailing_slash
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_MODEL_DELETE_FAILED,
    PROVIDER_MODEL_DELETED,
    PROVIDER_MODEL_PULL_COMPLETED,
    PROVIDER_MODEL_PULL_FAILED,
    PROVIDER_MODEL_PULL_STARTED,
)

logger = get_logger(__name__)

_PULL_TIMEOUT_SECONDS: float = 600.0
_DELETE_TIMEOUT_SECONDS: float = 30.0
_HTTP_OK: int = 200
_HTTP_NOT_FOUND: int = 404
_HTTP_CLIENT_ERROR: int = 400


class PullProgressEvent(BaseModel):
    """Progress event emitted during a model pull.

    Attributes:
        status: Human-readable status message from the provider.
        progress_percent: Download progress as a percentage (0-100).
        total_bytes: Total download size in bytes.
        completed_bytes: Bytes downloaded so far.
        error: Error message if the pull failed.
        done: Whether this is the final event.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    status: NotBlankStr
    progress_percent: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
    )
    total_bytes: int | None = Field(default=None, ge=0)
    completed_bytes: int | None = Field(default=None, ge=0)
    error: NotBlankStr | None = None
    done: bool = False

    @model_validator(mode="after")
    def _check_cross_field_invariants(self) -> PullProgressEvent:
        """Enforce cross-field consistency."""
        if self.error is not None and not self.done:
            msg = "error events must be terminal (done=True)"
            raise ValueError(msg)
        if (
            self.completed_bytes is not None
            and self.total_bytes is not None
            and self.completed_bytes > self.total_bytes
        ):
            msg = "completed_bytes cannot exceed total_bytes"
            raise ValueError(msg)
        return self


@runtime_checkable
class LocalModelManager(Protocol):
    """Protocol for local provider model management."""

    def pull_model(
        self,
        model_name: str,
    ) -> AsyncIterator[PullProgressEvent]:
        """Pull/download a model, yielding progress events.

        The last event always has ``done=True``. Error events carry
        ``error`` set and ``done=True``.

        Args:
            model_name: Model identifier (e.g. ``"llama3.2:1b"``).

        Yields:
            Progress events. The last event has ``done=True``.
        """
        ...

    async def delete_model(self, model_name: str) -> None:
        """Delete a model from the local provider.

        Args:
            model_name: Model identifier to delete.

        Raises:
            ValueError: If the model does not exist (HTTP 404).
            RuntimeError: If the delete request fails due to an
                upstream HTTP error or transport failure.
        """
        ...


class OllamaModelManager:
    """Model manager for Ollama instances.

    Uses Ollama's REST API:
    - ``POST /api/pull`` with streaming newline-delimited JSON
    - ``DELETE /api/delete`` with ``{"name": model}``
    """

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url or not base_url.strip():
            msg = "base_url must be a non-empty URL"
            raise ValueError(msg)
        self._base_url = strip_trailing_slash(base_url)
        self._client = client

    @staticmethod
    def _parse_pull_line(
        data: dict[str, object],
        model_name: str,
    ) -> PullProgressEvent:
        """Parse a single JSON line from the pull stream.

        Args:
            data: Parsed JSON dict from the stream.
            model_name: Model being pulled (for logging).

        Returns:
            A progress event derived from the data.
        """
        error = data.get("error")
        if error:
            logger.warning(
                PROVIDER_MODEL_PULL_FAILED,
                provider="ollama",
                model=model_name,
                error=error,
            )
            return PullProgressEvent(
                status=str(error),
                error=str(error),
                done=True,
            )

        status = str(data.get("status", "unknown"))
        total = data.get("total")
        completed = data.get("completed")
        progress = None
        if (
            isinstance(total, (int, float))
            and isinstance(completed, (int, float))
            and total > 0
        ):
            progress = min(
                round((completed / total) * 100, 1),
                100.0,
            )

        is_done = status == "success"
        if is_done:
            logger.info(
                PROVIDER_MODEL_PULL_COMPLETED,
                provider="ollama",
                model=model_name,
            )
        return PullProgressEvent(
            status=status,
            progress_percent=progress,
            total_bytes=total if isinstance(total, int) else None,
            completed_bytes=(completed if isinstance(completed, int) else None),
            done=is_done,
        )

    async def _consume_pull_stream(
        self,
        response: httpx.Response,
        model_name: str,
    ) -> AsyncIterator[PullProgressEvent]:
        """Iterate response lines and yield parsed progress events."""
        async for line in response.aiter_lines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                logger.warning(
                    PROVIDER_MODEL_PULL_FAILED,
                    provider="ollama",
                    model=model_name,
                    error=f"Malformed JSON in stream: {line[:200]!r}",
                )
                continue

            if not isinstance(data, dict):
                logger.warning(
                    PROVIDER_MODEL_PULL_FAILED,
                    provider="ollama",
                    model=model_name,
                    error=f"Non-object JSON in stream: {line[:200]!r}",
                )
                continue

            event = self._parse_pull_line(data, model_name)
            yield event
            if event.done:
                return

        # Stream ended without a terminal done=True event.
        err = "Stream ended without success status"
        logger.warning(
            PROVIDER_MODEL_PULL_FAILED,
            provider="ollama",
            model=model_name,
            error=err,
        )
        yield PullProgressEvent(
            status=err,
            error=err,
            done=True,
        )

    async def pull_model(
        self,
        model_name: str,
    ) -> AsyncIterator[PullProgressEvent]:
        """Pull a model from the Ollama library.

        Streams progress via newline-delimited JSON from Ollama's
        ``/api/pull`` endpoint.

        Args:
            model_name: Model name/tag (e.g. ``"llama3.2:1b"``).

        Yields:
            Progress events parsed from the stream.
        """
        logger.info(
            PROVIDER_MODEL_PULL_STARTED,
            provider="ollama",
            model=model_name,
        )
        url = f"{self._base_url}/api/pull"
        client = self._client or httpx.AsyncClient()
        owns_client = self._client is None
        try:
            async with client.stream(
                "POST",
                url,
                json={"name": model_name, "stream": True},
                timeout=_PULL_TIMEOUT_SECONDS,
            ) as response:
                if response.status_code != _HTTP_OK:
                    error_msg = f"Pull failed: HTTP {response.status_code}"
                    logger.warning(
                        PROVIDER_MODEL_PULL_FAILED,
                        provider="ollama",
                        model=model_name,
                        error=error_msg,
                    )
                    yield PullProgressEvent(
                        status=error_msg,
                        error=error_msg,
                        done=True,
                    )
                    return

                async for event in self._consume_pull_stream(
                    response,
                    model_name,
                ):
                    yield event
        except httpx.HTTPError as exc:
            err = f"HTTP error during pull: {safe_error_description(exc)}"
            logger.warning(
                PROVIDER_MODEL_PULL_FAILED,
                provider="ollama",
                model=model_name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            yield PullProgressEvent(
                status=err,
                error=err,
                done=True,
            )
        finally:
            if owns_client:
                await client.aclose()

    async def delete_model(self, model_name: str) -> None:
        """Delete a model from the Ollama instance.

        Args:
            model_name: Model name/tag to delete.

        Raises:
            ValueError: If the model does not exist (HTTP 404).
            RuntimeError: If the delete request fails due to an
                upstream HTTP error (status >= 400, non-404) or a
                transport failure (``httpx.HTTPError``).
        """
        url = f"{self._base_url}/api/delete"
        client = self._client or httpx.AsyncClient()
        owns_client = self._client is None
        try:
            response = await client.request(
                "DELETE",
                url,
                json={"name": model_name},
                timeout=_DELETE_TIMEOUT_SECONDS,
            )
            if response.status_code == _HTTP_NOT_FOUND:
                msg = f"Model {model_name!r} not found on Ollama instance"
                logger.warning(
                    PROVIDER_MODEL_DELETE_FAILED,
                    provider="ollama",
                    model=model_name,
                    error=msg,
                )
                raise ValueError(msg)
            if response.status_code >= _HTTP_CLIENT_ERROR:
                msg = f"Delete failed: HTTP {response.status_code}"
                logger.warning(
                    PROVIDER_MODEL_DELETE_FAILED,
                    provider="ollama",
                    model=model_name,
                    error=msg,
                )
                raise RuntimeError(msg)
            logger.info(
                PROVIDER_MODEL_DELETED,
                provider="ollama",
                model=model_name,
            )
        except httpx.HTTPError as exc:
            msg = f"HTTP error during delete: {safe_error_description(exc)}"
            logger.warning(
                PROVIDER_MODEL_DELETE_FAILED,
                provider="ollama",
                model=model_name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise RuntimeError(msg) from exc
        finally:
            if owns_client:
                await client.aclose()


def get_local_model_manager(
    preset_name: str | None,
    base_url: str,
) -> LocalModelManager | None:
    """Resolve a local model manager for the given preset.

    Args:
        preset_name: Preset identifier (e.g. ``"ollama"``).
        base_url: Provider base URL.

    Returns:
        A manager instance, or ``None`` if the preset does not
        support local model management.
    """
    if preset_name == "ollama":
        return OllamaModelManager(base_url=base_url)
    return None
