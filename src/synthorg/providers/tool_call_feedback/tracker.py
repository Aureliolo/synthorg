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
    Each returns ``True`` only when it actually changed the persisted flag.
    """

    async def mark_tool_calls_unverified(self, provider: str, model: str) -> bool:
        """Set ``tool_calls_verified=False`` (downgrade); ``True`` if changed."""
        ...

    async def mark_tool_calls_verified(self, provider: str, model: str) -> bool:
        """Re-enable a downgraded model (``False`` -> ``True``); ``True`` if changed.

        A no-op (returns ``False``) for a never-downgraded model: a success
        on an untested (``None``) model is not worth a provider-config
        rewrite, since the optimistic matcher already selects it.
        """
        ...

    async def clear_tool_calls_verification(self, provider: str, model: str) -> bool:
        """Set ``tool_calls_verified=None`` (manual reset); ``True`` if changed."""
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
        # Reset the flag first, then drop the accumulator under the lock.
        # The writer call is kept off the tracker lock so the tracker lock
        # is never held across the management service's own lock.
        await self._writer.clear_tool_calls_verification(provider, model)
        async with self._lock:
            self._cache.pop(key, None)
            await self._repo.delete(key)
            self._cache[key] = None
        logger.info(
            PROVIDER_TOOL_CALL_REENABLED,
            provider=provider,
            model=model,
            trigger="manual",
        )

    async def _on_failure(self, key: ModelToolCallSignalKey) -> None:
        """Decay, increment, persist, and downgrade past the threshold.

        Settings reads and the capability-writer downgrade are kept OFF the
        tracker lock: the lock guards only the cache read-modify-write so a
        slow settings DB read never serialises every other observation, and
        the tracker lock is never held across the management service's own
        lock (which the writer acquires).
        """
        provider, model = key
        threshold = await self._settings.get_int(_SETTINGS_NAMESPACE, _THRESHOLD_KEY)
        half_life = await self._settings.get_int(_SETTINGS_NAMESPACE, _HALF_LIFE_KEY)
        now = self._clock.now().timestamp()
        async with self._lock:
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
        """Clear the accumulator and re-enable a downgraded model.

        Re-enables BEFORE deleting the accumulator row so a later repo
        failure leaves the model usable and the row present (retried on the
        next success). A model with no accumulated failures is a pure
        in-memory no-op. The re-enable only writes for a genuinely
        downgraded (``False``) model, never an untested (``None``) one.
        """
        async with self._lock:
            row = await self._load(key)
        if row is None:
            return
        provider, model = key
        reenabled = await self._writer.mark_tool_calls_verified(provider, model)
        async with self._lock:
            self._cache.pop(key, None)
            await self._repo.delete(key)
            self._cache[key] = None
        logger.info(
            PROVIDER_TOOL_CALL_SUCCESS_OBSERVED,
            provider=provider,
            model=model,
            note="cleared",
        )
        if reenabled:
            logger.info(
                PROVIDER_TOOL_CALL_REENABLED,
                provider=provider,
                model=model,
                trigger="auto_recovery",
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

        Formula: ``prior_score * 0.5 ** (elapsed / half_life)``, where
        ``elapsed`` is floored at 0 to guard against clock regression.

        Returns:
            The exponentially-decayed score, or ``0.0`` when there is no
            prior row or a non-positive ``half_life`` (treated as instant
            decay -- a defence-in-depth guard against a mis-set setting,
            independent of the ``min_value=60`` definition bound).
        """
        if row is None or half_life <= 0:
            return 0.0
        elapsed = max(0.0, now - row.decayed_at)
        return float(row.failure_score * _DECAY_BASE ** (elapsed / half_life))
