"""Unit tests for AgentEngine personality-trim WebSocket notifier."""

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.core.agent import AgentIdentity
from synthorg.core.task import Task
from synthorg.engine.agent_engine import AgentEngine, PersonalityTrimPayload
from synthorg.settings.resolver import ConfigResolver
from tests._shared import mock_of

from .conftest import make_completion_response as _make_completion_response

if TYPE_CHECKING:
    from .conftest import MockCompletionProvider


def _make_resolver(
    *,
    trimming_enabled: bool = True,
    max_tokens_override: int = 10,
    notify_enabled: bool = True,
) -> MagicMock:
    """Build a ConfigResolver mock that returns the given ENGINE settings."""
    resolver = MagicMock()

    async def get_bool(namespace: str, key: str) -> bool:
        if key == "personality_trimming_enabled":
            return trimming_enabled
        if key == "personality_trimming_notify":
            return notify_enabled
        msg = f"unexpected get_bool({namespace}, {key})"
        raise AssertionError(msg)

    async def get_int(namespace: str, key: str) -> int:
        if key == "personality_max_tokens_override":
            return max_tokens_override
        msg = f"unexpected get_int({namespace}, {key})"
        raise AssertionError(msg)

    resolver.get_bool = AsyncMock(side_effect=get_bool)
    resolver.get_int = AsyncMock(side_effect=get_int)
    return resolver


def _make_sample_payload() -> PersonalityTrimPayload:
    """Build a representative ``PersonalityTrimPayload`` for unit-level tests."""
    return {
        "agent_id": "agent-1",
        "agent_name": "Test Agent",
        "task_id": "task-1",
        "before_tokens": 600,
        "after_tokens": 200,
        "max_tokens": 300,
        "trim_tier": 2,
        "budget_met": True,
    }


@pytest.mark.unit
class TestPersonalityTrimNotifier:
    """Tests for the personality_trim_notifier callback in AgentEngine.run()."""

    @pytest.mark.parametrize(
        (
            "trimming_enabled",
            "max_tokens_override",
            "notify_enabled",
            "notifier_provided",
            "expected_await_count",
        ),
        [
            # Trim fires + notify setting enabled => notifier awaited once.
            (True, 10, True, True, 1),
            # Trim fires + notify setting disabled => notifier suppressed.
            (True, 10, False, True, 0),
            # Trimming globally disabled => no trim info, no notify.  This
            # branch makes the "no notify" case independent of profile
            # default budgets: tightening the default personality budget
            # later cannot silently flip this matrix row.
            (False, 10, True, True, 0),
            # No notifier wired => run still succeeds, trimming proceeds silently.
            (True, 10, True, False, 0),
        ],
    )
    async def test_run_notifier_matrix(  # noqa: PLR0913
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
        *,
        trimming_enabled: bool,
        max_tokens_override: int,
        notify_enabled: bool,
        notifier_provided: bool,
        expected_await_count: int,
    ) -> None:
        """End-to-end ``run()`` matrix for the notifier/setting combinations."""
        notifier = AsyncMock() if notifier_provided else None
        resolver = _make_resolver(
            trimming_enabled=trimming_enabled,
            max_tokens_override=max_tokens_override,
            notify_enabled=notify_enabled,
        )
        provider = mock_provider_factory([_make_completion_response()])
        engine = AgentEngine(
            provider=provider,
            config_resolver=resolver,
            personality_trim_notifier=notifier,
        )

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert result.is_success is True
        if notifier is not None:
            assert notifier.await_count == expected_await_count

    async def test_notifier_payload_shape_when_trimming_fires(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """The forwarded payload has all 8 ``PersonalityTrimPayload`` keys."""
        notifier = AsyncMock()
        resolver = _make_resolver(
            trimming_enabled=True,
            max_tokens_override=10,
            notify_enabled=True,
        )
        provider = mock_provider_factory([_make_completion_response()])
        engine = AgentEngine(
            provider=provider,
            config_resolver=resolver,
            personality_trim_notifier=notifier,
        )

        await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert notifier.await_count == 1
        assert notifier.await_args is not None
        payload = notifier.await_args.args[0]
        assert set(payload.keys()) == {
            "agent_id",
            "agent_name",
            "task_id",
            "before_tokens",
            "after_tokens",
            "max_tokens",
            "trim_tier",
            "budget_met",
        }
        assert payload["agent_name"] == sample_agent_with_personality.name
        assert payload["max_tokens"] == 10
        assert isinstance(payload["before_tokens"], int)
        assert isinstance(payload["after_tokens"], int)
        # Trimming must actually reduce tokens -- guards against swapping
        # ``before_tokens``/``after_tokens`` keys in the payload builder.
        # Note: ``after_tokens`` may still exceed ``max_tokens`` when
        # trimming reaches tier 3 without meeting the budget
        # (``budget_met=False``), so we cannot assert the ``<=`` relation.
        assert payload["before_tokens"] > payload["after_tokens"]
        assert payload["trim_tier"] in {1, 2, 3}

    async def test_notifier_failure_is_swallowed(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Exceptions raised inside the notifier never break task execution."""
        notifier = AsyncMock(side_effect=RuntimeError("pub broken"))
        resolver = _make_resolver(
            trimming_enabled=True,
            max_tokens_override=10,
            notify_enabled=True,
        )
        provider = mock_provider_factory([_make_completion_response()])
        engine = AgentEngine(
            provider=provider,
            config_resolver=resolver,
            personality_trim_notifier=notifier,
        )

        # Should complete without raising even though notifier blows up.
        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert notifier.await_count == 1
        assert result.is_success is True

    async def test_notifier_timeout_is_swallowed(
        self,
        mock_provider_factory: type[MockCompletionProvider],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A notifier that exceeds the 2-second budget is cancelled and logged.

        The engine wraps the callback in :func:`asyncio.timeout` so a slow or
        hung external runner cannot stall the main execution path.  The
        timeout branch emits ``PROMPT_PERSONALITY_NOTIFY_FAILED`` with a
        distinct ``reason="notifier callback timed out"`` marker and does
        not re-raise.

        We patch the production 2s budget down to a small value so the test
        completes in milliseconds instead of waiting the real budget.  The
        patch targets ``asyncio.timeout`` on the global ``asyncio`` module
        because the engine code imports and uses it as ``asyncio.timeout``.
        """
        real_timeout = asyncio.timeout

        def fast_timeout(_seconds: float) -> object:
            return real_timeout(0.01)

        monkeypatch.setattr(asyncio, "timeout", fast_timeout)

        # Notifier that blocks indefinitely until cancelled -- use
        # asyncio.Event().wait() per CLAUDE.md guidance for
        # cancellation-safe "blocks forever" semantics.
        async def slow_notifier(_payload: PersonalityTrimPayload) -> None:
            await asyncio.Event().wait()

        provider = mock_provider_factory([_make_completion_response()])
        engine = AgentEngine(
            provider=provider,
            personality_trim_notifier=slow_notifier,
        )

        # Must not raise -- TimeoutError is swallowed by the best-effort guard.
        await engine._maybe_notify_personality_trim(_make_sample_payload())

    async def test_notifier_fail_open_when_setting_read_fails(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """When ``config_resolver.get_bool`` raises, the notifier still fires.

        The fail-open contract: a transient settings-store failure must not
        silently disable notifications that the operator enabled.  The method
        logs ``PROMPT_PERSONALITY_NOTIFY_FAILED`` with a ``fail-open`` reason
        and proceeds with the built-in default ``notify_enabled=True``.
        """
        notifier = AsyncMock()
        resolver = mock_of[ConfigResolver](
            get_bool=AsyncMock(side_effect=RuntimeError("db down")),
            get_int=AsyncMock(return_value=10),
        )
        provider = mock_provider_factory([_make_completion_response()])
        engine = AgentEngine(
            provider=provider,
            config_resolver=resolver,
            personality_trim_notifier=notifier,
        )

        # Note: this test exercises the private method directly to cover the
        # setting-read-failure branch deterministically.  An equivalent
        # end-to-end test via engine.run() would require a resolver that
        # raises only for ``personality_trimming_notify`` while succeeding
        # for ``personality_trimming_enabled`` -- the direct call is simpler
        # and documented as a private-API coupling in the docstring above.
        await engine._maybe_notify_personality_trim(_make_sample_payload())

        assert notifier.await_count == 1

    async def test_notifier_fires_without_config_resolver(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """When config_resolver is None, the notify setting defaults to enabled.

        Covers the ``self._config_resolver is None`` branch in
        ``_maybe_notify_personality_trim`` -- without a resolver the default
        behavior is to fire the notifier (opt-out only via explicit setting).

        Note: calls the private ``_maybe_notify_personality_trim`` method
        directly to test the no-resolver branch deterministically.  If the
        method is ever renamed or inlined, this test will break at import
        time and is straightforward to fix; the trade-off is accepted for
        branch coverage.
        """
        notifier = AsyncMock()
        provider = mock_provider_factory([_make_completion_response()])
        engine = AgentEngine(
            provider=provider,
            personality_trim_notifier=notifier,
            # config_resolver intentionally omitted
        )

        payload = _make_sample_payload()
        await engine._maybe_notify_personality_trim(payload)

        assert notifier.await_count == 1
        assert notifier.await_args is not None
        assert notifier.await_args.args[0] == payload

    @pytest.mark.parametrize(
        "exc_type",
        [asyncio.CancelledError, MemoryError, RecursionError],
    )
    async def test_base_exceptions_propagate_through_notifier(
        self,
        mock_provider_factory: type[MockCompletionProvider],
        exc_type: type[BaseException],
    ) -> None:
        """``BaseException`` subclasses raised by the notifier must propagate.

        ``asyncio.CancelledError``, ``MemoryError``, and ``RecursionError``
        are all ``BaseException`` subclasses (or explicitly re-raised in the
        case of ``MemoryError``/``RecursionError``) and must never be
        swallowed by the best-effort ``except Exception`` guard, so that
        task cancellation and low-level runtime failures propagate correctly
        through the engine.
        """
        notifier = AsyncMock(side_effect=exc_type())
        provider = mock_provider_factory([_make_completion_response()])
        engine = AgentEngine(
            provider=provider,
            personality_trim_notifier=notifier,
        )

        with pytest.raises(exc_type):
            await engine._maybe_notify_personality_trim(
                _make_sample_payload(),
            )

    @pytest.mark.parametrize(
        "exc_type",
        [asyncio.CancelledError, MemoryError, RecursionError],
    )
    async def test_base_exceptions_propagate_through_setting_read(
        self,
        mock_provider_factory: type[MockCompletionProvider],
        exc_type: type[BaseException],
    ) -> None:
        """``BaseException`` raised by the setting read must propagate.

        Covers the second ``except MemoryError, RecursionError:`` / fall-through
        branch in ``_maybe_notify_personality_trim``: when ``get_bool`` raises
        a ``BaseException`` subclass, the method must not swallow it.
        """
        notifier = AsyncMock()
        resolver = mock_of[ConfigResolver](
            get_bool=AsyncMock(side_effect=exc_type()),
            get_int=AsyncMock(return_value=10),
        )
        provider = mock_provider_factory([_make_completion_response()])
        engine = AgentEngine(
            provider=provider,
            config_resolver=resolver,
            personality_trim_notifier=notifier,
        )

        with pytest.raises(exc_type):
            await engine._maybe_notify_personality_trim(
                _make_sample_payload(),
            )
