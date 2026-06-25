"""Time-decayed tool-call failure tracker (the installed signal sink).

Accumulates a per-``(provider, model)`` exponentially time-decayed
failure score from observations at the provider boundary. When the score
crosses the operator-configured threshold the model is downgraded
(``ModelMetadata.tool_calls_verified`` -> ``False``) via the
capability-writer so the matcher stops assigning it to tool-requiring
agents. A genuine tool-call success clears the accumulator and
re-enables a downgraded model.

The tracker keeps an in-memory cache hydrated lazily from the repository
(one read per key per process), so a steady-state success on a healthy
model is a pure in-memory no-op. It swallows all of its own errors
(``record`` is awaited inside the provider hot path), and re-reads the
threshold / half-life / enabled settings live each observation so
operator changes apply without a restart.
"""

import asyncio
from typing import Final, Protocol, runtime_checkable

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_TOOL_CALL_DOWNGRADED,
    PROVIDER_TOOL_CALL_FAILURE_OBSERVED,
    PROVIDER_TOOL_CALL_FEEDBACK_RECORD_FAILED,
    PROVIDER_TOOL_CALL_REENABLED,
    PROVIDER_TOOL_CALL_SUCCESS_OBSERVED,
)
from synthorg.persistence.model_tool_call_signal_protocol import (
    ModelToolCallSignal,
    ModelToolCallSignalKey,
    ModelToolCallSignalRepository,
)
from synthorg.providers.tool_call_feedback.sink import ToolCallOutcome

logger = get_logger(__name__)

_SETTINGS_NAMESPACE: Final[str] = "providers"
_ENABLED_KEY: Final[str] = "tool_call_feedback_enabled"
_THRESHOLD_KEY: Final[str] = "tool_call_failure_threshold"
_HALF_LIFE_KEY: Final[str] = "tool_call_failure_decay_half_life_seconds"

# Exponential-decay base: a failure's weight halves every half-life.
_DECAY_BASE: Final[float] = 0.5


@runtime_checkable
class ToolCallFeedbackSettings(Protocol):
    """Live settings reader for the tracker (satisfied by ``ConfigResolver``)."""

    async def get_bool(self, namespace: str, key: str) -> bool:
        """Resolve a boolean setting (DB > env > code)."""
        ...

    async def get_int(self, namespace: str, key: str) -> int:
        """Resolve an integer setting (DB > env > code)."""
        ...


@runtime_checkable
class ToolCallCapabilityWriter(Protocol):
    """Persists the durable ``tool_calls_verified`` capability decision.

    Satisfied structurally by ``ProviderManagementService``. Each method
    is idempotent and a no-op when the model's current flag already
    matches the target, so the tracker can call it freely without
    triggering a redundant provider-config rewrite + registry hot-reload.
    """

    async def mark_tool_calls_unverified(self, provider: str, model: str) -> None:
        """Set ``tool_calls_verified=False`` (downgrade)."""
        ...

    async def mark_tool_calls_verified(self, provider: str, model: str) -> None:
        """Set ``tool_calls_verified=True`` (auto-recovery, proven)."""
        ...

    async def clear_tool_calls_verification(self, provider: str, model: str) -> None:
        """Set ``tool_calls_verified=None`` (manual reset, optimism resumes)."""
        ...


class ToolCallFeedbackTracker:
    """In-memory decay accumulator backed by a durable signal repository.

    Args:
        repo: The persisted decay-accumulator repository.
        writer: Persists the durable ``tool_calls_verified`` decision.
        settings: Live settings reader for threshold / half-life / enabled.
        clock: Injectable clock; tests inject ``FakeClock``. Defaults to
            ``SystemClock``.
    """

    def __init__(
        self,
        *,
        repo: ModelToolCallSignalRepository,
        writer: ToolCallCapabilityWriter,
        settings: ToolCallFeedbackSettings,
        clock: Clock | None = None,
    ) -> None:
        self._repo = repo
        self._writer = writer
        self._settings = settings
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._lock = asyncio.Lock()
        # Lazily-hydrated cache: ``None`` means "no row" (a healthy model),
        # so a steady-state success never re-reads the database.
        self._cache: dict[ModelToolCallSignalKey, ModelToolCallSignal | None] = {}

    async def record(
        self,
        *,
        provider: str,
        model: str,
        outcome: ToolCallOutcome,
    ) -> None:
        """Record one tool-call outcome (never raises into the caller).

        Args:
            provider: SynthOrg provider registry key.
            model: Model identifier within the provider.
            outcome: The observed :class:`ToolCallOutcome`.
        """
        try:
            if not await self._settings.get_bool(_SETTINGS_NAMESPACE, _ENABLED_KEY):
                return
            key = (NotBlankStr(provider), NotBlankStr(model))
            async with self._lock:
                if outcome is ToolCallOutcome.FAILURE:
                    await self._on_failure(key)
                else:
                    await self._on_success(key)
        except Exception as exc:  # noqa: BLE001 -- awaited in the provider hot path; criticals re-raised, all else swallowed + logged
            reraise_critical(exc)
            logger.warning(
                PROVIDER_TOOL_CALL_FEEDBACK_RECORD_FAILED,
                provider=provider,
                model=model,
                outcome=outcome.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def clear(self, *, provider: str, model: str) -> None:
        """Clear a model's accumulator and reset its capability flag.

        Used by the manual operator "re-enable tool calling" action: drops
        the decay row and sets ``tool_calls_verified`` back to ``None``
        (untested) so the matcher's optimistic path resumes.

        Args:
            provider: SynthOrg provider registry key.
            model: Model identifier within the provider.
        """
        key = (NotBlankStr(provider), NotBlankStr(model))
        async with self._lock:
            await self._writer.clear_tool_calls_verification(provider, model)
            await self._repo.delete(key)
            self._cache[key] = None
            logger.info(
                PROVIDER_TOOL_CALL_REENABLED,
                provider=provider,
                model=model,
                trigger="manual",
            )

    async def _on_failure(self, key: ModelToolCallSignalKey) -> None:
        """Decay, increment, persist, and downgrade past the threshold."""
        provider, model = key
        threshold = await self._settings.get_int(_SETTINGS_NAMESPACE, _THRESHOLD_KEY)
        half_life = await self._settings.get_int(_SETTINGS_NAMESPACE, _HALF_LIFE_KEY)
        now = self._clock.now().timestamp()
        prior = self._decayed(await self._load(key), now, half_life)
        score = prior + 1.0
        signal = ModelToolCallSignal(
            provider_name=provider,
            model_id=model,
            failure_score=score,
            decayed_at=now,
        )
        await self._repo.save(signal)
        self._cache[key] = signal
        logger.info(
            PROVIDER_TOOL_CALL_FAILURE_OBSERVED,
            provider=provider,
            model=model,
            failure_score=score,
            threshold=threshold,
        )
        if score >= threshold:
            await self._writer.mark_tool_calls_unverified(provider, model)
            logger.warning(
                PROVIDER_TOOL_CALL_DOWNGRADED,
                provider=provider,
                model=model,
                failure_score=score,
                threshold=threshold,
            )

    async def _on_success(self, key: ModelToolCallSignalKey) -> None:
        """Clear the accumulator and re-enable a downgraded model."""
        row = await self._load(key)
        if row is None:
            # Healthy model with no accumulated failures: pure no-op.
            return
        provider, model = key
        # The writer no-ops unless the flag is currently False, so this is
        # cheap for a model that accumulated sub-threshold failures without
        # ever being downgraded.
        await self._writer.mark_tool_calls_verified(provider, model)
        await self._repo.delete(key)
        self._cache[key] = None
        logger.info(
            PROVIDER_TOOL_CALL_SUCCESS_OBSERVED,
            provider=provider,
            model=model,
            note="cleared",
        )

    async def _load(self, key: ModelToolCallSignalKey) -> ModelToolCallSignal | None:
        """Return the cached row, hydrating once from the repository."""
        if key in self._cache:
            return self._cache[key]
        row = await self._repo.get(key)
        self._cache[key] = row
        return row

    def _decayed(
        self,
        row: ModelToolCallSignal | None,
        now: float,
        half_life: int,
    ) -> float:
        """Return ``row``'s failure score decayed forward to ``now``.

        Returns:
            The exponentially-decayed score, or ``0.0`` when there is no
            prior row.
        """
        if row is None:
            return 0.0
        elapsed = max(0.0, now - row.decayed_at)
        return float(row.failure_score * _DECAY_BASE ** (elapsed / half_life))
