"""Integration tests for retry + rate limiting on capability lookups.

Mirrors ``test_retry_integration.py`` but exercises the
``get_model_capabilities()`` and ``batch_get_capabilities()`` paths.
The CLAUDE.md contract states "all provider calls go through
BaseCompletionProvider" -- these tests assert that capability lookups
honour the same retry handler + rate limiter as ``complete()`` /
``stream()``.

The LiteLLM driver overrides ``batch_get_capabilities`` with a tight
in-process loop over its preset catalog, so tests stub the
``_do_get_model_capabilities`` boundary on the provider directly to
exercise the resilience path that would matter for any future driver
that does network I/O on capability lookups.
"""

import asyncio

import pytest

from synthorg.config.schema import ProviderConfig, ProviderModelConfig
from synthorg.core.resilience_config import RateLimiterConfig, RetryConfig
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.drivers.litellm_driver import LiteLLMDriver
from synthorg.providers.errors import (
    AuthenticationError,
    ProviderTimeoutError,
)
from synthorg.providers.resilience.errors import RetryExhaustedError

pytestmark = pytest.mark.integration


def _make_config(
    *,
    max_retries: int = 2,
    max_requests_per_minute: int = 0,
    max_concurrent: int = 0,
) -> ProviderConfig:
    return ProviderConfig(
        driver="litellm",
        connection_name="provider-capability-test",
        models=(
            ProviderModelConfig(
                id="test-model-001",
                alias="test-model",
                cost_per_1k_input=0.001,
                cost_per_1k_output=0.002,
            ),
            ProviderModelConfig(
                id="test-model-002",
                alias="test-model-2",
                cost_per_1k_input=0.001,
                cost_per_1k_output=0.002,
            ),
        ),
        retry=RetryConfig(
            max_retries=max_retries,
            base_delay=0.001,
            max_delay=0.01,
            jitter=False,
        ),
        rate_limiter=RateLimiterConfig(
            max_requests_per_minute=max_requests_per_minute,
            max_concurrent=max_concurrent,
        ),
    )


def _stub_capabilities(model_id: str) -> ModelCapabilities:
    return ModelCapabilities(
        model_id=model_id,
        provider="test-provider",
        max_context_tokens=8_000,
        max_output_tokens=2_000,
        cost_per_1k_input=0.001,
        cost_per_1k_output=0.002,
        supports_tools=True,
        supports_vision=False,
    )


def _make_rate_limited_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[LiteLLMDriver, dict[str, int], dict[str, asyncio.Event]]:
    """Build a ``max_concurrent=1`` driver wired for deterministic overlap.

    Returns the driver, the shared ``in_flight`` counter dict, and
    three events:

    - ``first_entered``: set by the first task once it has crossed
      into the rate-limited section (i.e. ``in_flight["current"] == 1``).
    - ``second_entered``: set by the second task if it ever enters
      the rate-limited section.  The orchestrator asserts this is
      *unset* before releasing the first -- i.e. proves the rate
      limiter is actively holding the second task back.  If the wrap
      regressed to ``max_concurrent=0`` the second would slip through
      and the assertion would catch it deterministically (no peak
      racing required).
    - ``release_first``: awaited by the first task; the orchestrator
      sets this once the second task has been spawned and verified to
      be blocked, so the first task can exit and free the rate-limit
      slot.

    Wall-clock sleeps would be flaky under CI load and mask what the
    test is actually trying to assert about the rate limiter.
    """
    driver = LiteLLMDriver("test-provider", _make_config(max_concurrent=1))
    in_flight = {"current": 0, "peak": 0}
    counter_lock = asyncio.Lock()
    first_entered = asyncio.Event()
    second_entered = asyncio.Event()
    release_first = asyncio.Event()

    async def _stub_do(model: str) -> ModelCapabilities:
        async with counter_lock:
            in_flight["current"] += 1
            in_flight["peak"] = max(in_flight["peak"], in_flight["current"])
            if model == "test-model-001":
                first_entered.set()
            elif model == "test-model-002":
                second_entered.set()
        if model == "test-model-001":
            await release_first.wait()
        async with counter_lock:
            in_flight["current"] -= 1
        return _stub_capabilities(model)

    monkeypatch.setattr(driver, "_do_get_model_capabilities", _stub_do)
    return (
        driver,
        in_flight,
        {
            "first_entered": first_entered,
            "second_entered": second_entered,
            "release_first": release_first,
        },
    )


async def _race_two_capability_tasks(
    driver: LiteLLMDriver,
    events: dict[str, asyncio.Event],
) -> None:
    """Spawn two ``get_model_capabilities`` calls in serial-via-rate-limit.

    The first task is started, allowed to enter the rate-limited
    section, then held; the second task is spawned and given a
    scheduling tick to make progress.  If the rate limiter is
    working, the second task is parked on it and ``second_entered``
    stays unset; if it regressed, the second task slips through and
    the assertion below trips deterministically.  Then the first is
    released; both are awaited.
    """
    first_task = asyncio.create_task(
        driver.get_model_capabilities("test-model-001"),
    )
    await events["first_entered"].wait()
    second_task = asyncio.create_task(
        driver.get_model_capabilities("test-model-002"),
    )
    # Give the event loop one tick so the second task gets scheduled.
    # If the rate limiter regressed, the task would have entered
    # ``_stub_do`` and set ``second_entered``.  Otherwise it is parked
    # on the limiter and ``second_entered`` stays unset.
    await asyncio.sleep(0)
    assert not events["second_entered"].is_set(), (
        "rate limiter regression: second task entered the critical "
        "section while the first still holds the slot"
    )
    events["release_first"].set()
    await asyncio.gather(first_task, second_task)


class TestGetModelCapabilitiesResilience:
    """``get_model_capabilities`` honours retry + rate-limit budget."""

    async def test_succeeds_after_transient_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Transient ProviderTimeoutError on first call; second succeeds."""
        driver = LiteLLMDriver("test-provider", _make_config())
        call_count = {"n": 0}

        async def _stub_do(model: str) -> ModelCapabilities:
            call_count["n"] += 1
            if call_count["n"] < 2:
                msg = "transient"
                raise ProviderTimeoutError(
                    msg,
                    context={"provider": "test-provider", "model": model},
                )
            return _stub_capabilities(model)

        monkeypatch.setattr(driver, "_do_get_model_capabilities", _stub_do)
        result = await driver.get_model_capabilities("test-model-001")
        assert result.model_id == "test-model-001"
        assert call_count["n"] == 2

    async def test_exhausts_retries_raises_retry_exhausted(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """All attempts raise transient; ``RetryExhaustedError`` propagates."""
        driver = LiteLLMDriver("test-provider", _make_config(max_retries=2))

        async def _stub_do(model: str) -> ModelCapabilities:
            msg = "always transient"
            raise ProviderTimeoutError(
                msg,
                context={"provider": "test-provider", "model": model},
            )

        monkeypatch.setattr(driver, "_do_get_model_capabilities", _stub_do)
        with pytest.raises(RetryExhaustedError) as exc_info:
            await driver.get_model_capabilities("test-model-001")
        assert isinstance(exc_info.value.original_error, ProviderTimeoutError)

    async def test_non_retryable_not_retried(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``AuthenticationError`` is not retryable; surfaces immediately."""
        driver = LiteLLMDriver("test-provider", _make_config())
        call_count = {"n": 0}

        async def _stub_do(model: str) -> ModelCapabilities:
            call_count["n"] += 1
            msg = "bad key"
            raise AuthenticationError(
                msg,
                context={"provider": "test-provider", "model": model},
            )

        monkeypatch.setattr(driver, "_do_get_model_capabilities", _stub_do)
        with pytest.raises(AuthenticationError):
            await driver.get_model_capabilities("test-model-001")
        assert call_count["n"] == 1

    async def test_consumes_rate_limiter_slot(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``max_concurrent=1`` serialises two parallel capability lookups.

        Uses an ``asyncio.Lock`` around the counter so the assertion does
        not depend on the rate limiter for atomicity. If the wrap regresses
        to ``max_concurrent=0`` the counter would still update consistently
        and the peak assertion would catch the parallelism.
        """
        driver, in_flight, events = _make_rate_limited_driver(monkeypatch)
        await _race_two_capability_tasks(driver, events)
        # ``max_concurrent=1`` means only one in-flight at a time.  If
        # the rate limiter was bypassed, ``peak`` would reach 2.
        assert in_flight["peak"] == 1


class TestBatchGetCapabilitiesResilience:
    """``BaseCompletionProvider.batch_get_capabilities`` default impl."""

    async def test_surfaces_retry_exhausted_via_exception_group(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``RetryExhaustedError`` propagates out of the TaskGroup batch.

        ``asyncio.TaskGroup`` wraps any in-flight raised exception in an
        ``ExceptionGroup``; the test asserts the wrapper contract
        explicitly so a future refactor that loses the wrapping is
        caught.
        """
        driver = LiteLLMDriver("test-provider", _make_config(max_retries=1))

        async def _stub_do(model: str) -> ModelCapabilities:
            msg = "transient"
            raise ProviderTimeoutError(
                msg,
                context={"provider": "test-provider", "model": model},
            )

        monkeypatch.setattr(driver, "_do_get_model_capabilities", _stub_do)
        # The LiteLLM override has its own in-process batch path; call
        # the base default directly to exercise the per-model fan-out.
        from synthorg.providers.base import BaseCompletionProvider

        with pytest.raises(ExceptionGroup) as exc_info:
            await BaseCompletionProvider.batch_get_capabilities(
                driver,
                ("test-model-001", "test-model-002"),
            )
        inner = exc_info.value.exceptions
        assert any(isinstance(e, RetryExhaustedError) for e in inner)

    @pytest.mark.parametrize(
        "exception_factory",
        [
            pytest.param(MemoryError, id="memory-error"),
            pytest.param(RecursionError, id="recursion-error"),
        ],
    )
    async def test_propagates_resource_exhaustion_errors(
        self,
        monkeypatch: pytest.MonkeyPatch,
        exception_factory: type[Exception],
    ) -> None:
        """``MemoryError`` / ``RecursionError`` escape uncaught.

        Both are :class:`Exception` subclasses (not :class:`BaseException`
        directly) but they signal runtime resource exhaustion -- the
        batch loop must NOT degrade them to ``None`` (would silently
        mask a critical signal).  Verifies the
        ``except (MemoryError, RecursionError, RetryExhaustedError)``
        re-raise path in ``_one()``.
        """
        driver = LiteLLMDriver("test-provider", _make_config(max_retries=0))

        async def _stub_do(model: str) -> ModelCapabilities:
            del model
            raise exception_factory()

        monkeypatch.setattr(driver, "_do_get_model_capabilities", _stub_do)
        from synthorg.providers.base import BaseCompletionProvider

        with pytest.raises(ExceptionGroup) as exc_info:
            await BaseCompletionProvider.batch_get_capabilities(
                driver,
                ("test-model-001",),
            )
        assert any(isinstance(e, exception_factory) for e in exc_info.value.exceptions)

    async def test_swallows_classification_errors(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Per-model non-retryable errors degrade to ``None`` entries."""
        driver = LiteLLMDriver("test-provider", _make_config(max_retries=0))

        async def _stub_do(model: str) -> ModelCapabilities:
            if model == "test-model-001":
                msg = "bad-key"
                raise AuthenticationError(
                    msg,
                    context={"provider": "test-provider", "model": model},
                )
            return _stub_capabilities(model)

        monkeypatch.setattr(driver, "_do_get_model_capabilities", _stub_do)
        from synthorg.providers.base import BaseCompletionProvider

        # Per-model non-retryable errors collapse to ``None`` while the
        # rest of the batch resolves normally -- this is the expected
        # behaviour. Only ``RetryExhaustedError`` / ``MemoryError`` /
        # ``RecursionError`` propagate.
        result = await BaseCompletionProvider.batch_get_capabilities(
            driver,
            ("test-model-001", "test-model-002"),
        )
        assert result["test-model-001"] is None
        assert result["test-model-002"] is not None
        assert result["test-model-002"].model_id == "test-model-002"

    async def test_litellm_driver_batch_unaffected(self) -> None:
        """Sanity: LiteLLM's in-process batch override returns shape-stable."""
        driver = LiteLLMDriver("test-provider", _make_config())
        result = await driver.batch_get_capabilities(("test-model-001",))
        assert "test-model-001" in result
        assert result["test-model-001"] is not None
        assert result["test-model-001"].model_id == "test-model-001"
