"""Tests for ``PerOpRateLimitSettingsSubscriber``.

The subscriber glues the SettingsService polling loop to the live
per-op configs on AppState.  Tests cover: protocol conformance,
happy-path rebuild for both guards, malformed JSON recovery (keeps
existing config), and boundary-case override coercion
(list-of-pairs -> tuple-of-ints).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.api.rate_limits.config import PerOpRateLimitConfig
from synthorg.api.rate_limits.inflight_config import PerOpConcurrencyConfig
from synthorg.api.state import AppState
from synthorg.api.state_per_op_limits import PerOpLimitsState
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.per_op_rate_limit_subscriber import (
    PerOpRateLimitSettingsSubscriber,
)

pytestmark = pytest.mark.unit


def _settings_map_to_async_mock(values: dict[tuple[str, str], str]) -> AsyncMock:
    """Build an AsyncMock that returns canned setting values.

    ``values`` maps ``(namespace, key)`` to the raw string the
    ``SettingsService.get`` call should return as ``.value``.
    """

    async def _get(namespace: str, key: str) -> MagicMock:
        result = MagicMock()
        result.value = values.get((namespace, key), "")
        return result

    return AsyncMock(side_effect=_get)


def _make_subscriber(
    settings_values: dict[tuple[str, str], str] | None = None,
    *,
    existing_rl: PerOpRateLimitConfig | None = None,
    existing_concurrency: PerOpConcurrencyConfig | None = None,
) -> tuple[
    PerOpRateLimitSettingsSubscriber,
    MagicMock,
]:
    """Build a subscriber with a mock AppState + SettingsService.

    Returns the subscriber plus the AppState mock so the caller can
    assert on its ``per_op_limits.swap_*`` calls.
    """
    settings_service = MagicMock(spec=SettingsService)
    settings_service.get = _settings_map_to_async_mock(settings_values or {})

    per_op = MagicMock(spec=PerOpLimitsState)
    per_op.has_rate_limit_config = existing_rl is not None
    per_op.has_concurrency_config = existing_concurrency is not None
    per_op.rate_limit_config = (
        existing_rl if existing_rl is not None else PerOpRateLimitConfig()
    )
    per_op.concurrency_config = (
        existing_concurrency
        if existing_concurrency is not None
        else PerOpConcurrencyConfig()
    )

    app_state = MagicMock(spec=AppState)
    app_state.per_op_limits = per_op

    sub = PerOpRateLimitSettingsSubscriber(
        app_state=app_state,
        settings_service=settings_service,
    )
    return sub, app_state


class TestSubscriberProtocol:
    """``PerOpRateLimitSettingsSubscriber`` conforms to ``SettingsSubscriber``."""

    def test_isinstance_check(self) -> None:
        sub, _ = _make_subscriber()
        assert isinstance(sub, SettingsSubscriber)

    def test_watched_keys_cover_both_guards(self) -> None:
        sub, _ = _make_subscriber()
        expected = frozenset(
            {
                ("api", "per_op_rate_limit_enabled"),
                ("api", "per_op_rate_limit_overrides"),
                ("api", "per_op_concurrency_enabled"),
                ("api", "per_op_concurrency_overrides"),
            }
        )
        assert sub.watched_keys == expected

    def test_subscriber_name(self) -> None:
        sub, _ = _make_subscriber()
        assert sub.subscriber_name == "per-op-rate-limit-settings"


class TestRateLimitRebuild:
    """``on_settings_changed`` rebuilds + swaps the sliding-window config."""

    async def test_enabled_change_rebuilds_and_swaps(self) -> None:
        sub, app_state = _make_subscriber(
            {
                ("api", "per_op_rate_limit_enabled"): "false",
                ("api", "per_op_rate_limit_overrides"): '{"memory.fine_tune": [2, 60]}',
            }
        )

        await sub.on_settings_changed("api", "per_op_rate_limit_enabled")

        app_state.per_op_limits.swap_rate_limit_config.assert_called_once()
        swapped: PerOpRateLimitConfig = (
            app_state.per_op_limits.swap_rate_limit_config.call_args[0][0]
        )
        assert swapped.enabled is False
        assert swapped.overrides["memory.fine_tune"] == (2, 60)

    async def test_overrides_change_triggers_same_path(self) -> None:
        sub, app_state = _make_subscriber(
            {
                ("api", "per_op_rate_limit_enabled"): "true",
                ("api", "per_op_rate_limit_overrides"): '{"agents.create": [3, 90]}',
            }
        )

        await sub.on_settings_changed("api", "per_op_rate_limit_overrides")

        app_state.per_op_limits.swap_rate_limit_config.assert_called_once()
        swapped: PerOpRateLimitConfig = (
            app_state.per_op_limits.swap_rate_limit_config.call_args[0][0]
        )
        assert swapped.enabled is True
        assert swapped.overrides["agents.create"] == (3, 90)

    async def test_existing_backend_is_preserved_on_swap(self) -> None:
        # Backend is restart_required so the subscriber never reads it
        # from the DB, but it must carry over from the existing config
        # so the swap does not accidentally drop a non-default backend.
        existing = PerOpRateLimitConfig(enabled=True)
        sub, app_state = _make_subscriber(
            {
                ("api", "per_op_rate_limit_enabled"): "true",
                ("api", "per_op_rate_limit_overrides"): "{}",
            },
            existing_rl=existing,
        )
        await sub.on_settings_changed("api", "per_op_rate_limit_overrides")
        swapped = app_state.per_op_limits.swap_rate_limit_config.call_args[0][0]
        assert swapped.backend == existing.backend

    async def test_malformed_json_raises_does_not_swap(self) -> None:
        sub, app_state = _make_subscriber(
            {
                ("api", "per_op_rate_limit_enabled"): "true",
                ("api", "per_op_rate_limit_overrides"): "not-json{",
            }
        )
        with pytest.raises(Exception):  # noqa: B017,PT011 -- dispatcher catches
            await sub.on_settings_changed("api", "per_op_rate_limit_overrides")
        app_state.per_op_limits.swap_rate_limit_config.assert_not_called()

    async def test_bad_override_shape_raises_does_not_swap(self) -> None:
        sub, app_state = _make_subscriber(
            {
                ("api", "per_op_rate_limit_enabled"): "true",
                ("api", "per_op_rate_limit_overrides"): '{"agents.create": 10}',
            }
        )
        with pytest.raises(Exception):  # noqa: B017,PT011
            await sub.on_settings_changed("api", "per_op_rate_limit_overrides")
        app_state.per_op_limits.swap_rate_limit_config.assert_not_called()


class TestConcurrencyRebuild:
    """``on_settings_changed`` rebuilds + swaps the inflight config."""

    async def test_enabled_change_rebuilds_and_swaps(self) -> None:
        sub, app_state = _make_subscriber(
            {
                ("api", "per_op_concurrency_enabled"): "false",
                ("api", "per_op_concurrency_overrides"): '{"memory.fine_tune": 2}',
            }
        )

        await sub.on_settings_changed("api", "per_op_concurrency_enabled")

        app_state.per_op_limits.swap_concurrency_config.assert_called_once()
        swapped: PerOpConcurrencyConfig = (
            app_state.per_op_limits.swap_concurrency_config.call_args[0][0]
        )
        assert swapped.enabled is False
        assert swapped.overrides["memory.fine_tune"] == 2

    async def test_overrides_change_triggers_same_path(self) -> None:
        sub, app_state = _make_subscriber(
            {
                ("api", "per_op_concurrency_enabled"): "true",
                ("api", "per_op_concurrency_overrides"): '{"providers.pull_model": 1}',
            }
        )

        await sub.on_settings_changed("api", "per_op_concurrency_overrides")

        swapped = app_state.per_op_limits.swap_concurrency_config.call_args[0][0]
        assert swapped.overrides == {"providers.pull_model": 1}

    async def test_non_object_json_raises_does_not_swap(self) -> None:
        sub, app_state = _make_subscriber(
            {
                ("api", "per_op_concurrency_enabled"): "true",
                ("api", "per_op_concurrency_overrides"): "[1, 2, 3]",
            }
        )
        with pytest.raises(Exception):  # noqa: B017,PT011
            await sub.on_settings_changed("api", "per_op_concurrency_overrides")
        app_state.per_op_limits.swap_concurrency_config.assert_not_called()


class TestUnexpectedRouting:
    """Unexpected namespace / key are logged and no-op, not crash."""

    async def test_unknown_namespace_is_ignored(self) -> None:
        sub, app_state = _make_subscriber()
        await sub.on_settings_changed("other", "per_op_rate_limit_enabled")
        app_state.per_op_limits.swap_rate_limit_config.assert_not_called()
        app_state.per_op_limits.swap_concurrency_config.assert_not_called()

    async def test_unknown_key_in_api_namespace_is_ignored(self) -> None:
        sub, app_state = _make_subscriber()
        await sub.on_settings_changed("api", "some_unrelated_key")
        app_state.per_op_limits.swap_rate_limit_config.assert_not_called()
        app_state.per_op_limits.swap_concurrency_config.assert_not_called()


class TestStrictValidation:
    """Malformed DB values abort the swap instead of silently applying."""

    async def test_invalid_bool_string_raises(self) -> None:
        # A typo like ``"yes"`` must not silently disable the guard
        # by collapsing to ``False`` at the old ``.lower() == "true"``
        # check.  The swap is aborted and the previous config stays.
        sub, app_state = _make_subscriber(
            {
                ("api", "per_op_rate_limit_enabled"): "yes",
                ("api", "per_op_rate_limit_overrides"): "{}",
            }
        )
        with pytest.raises(ValueError, match="not a valid boolean"):
            await sub.on_settings_changed("api", "per_op_rate_limit_enabled")
        app_state.per_op_limits.swap_rate_limit_config.assert_not_called()

    async def test_float_override_rejected(self) -> None:
        # ``1.9`` would be silently truncated to ``1`` by blind
        # ``int(...)`` -- the strict validator rejects non-integers so
        # the operator sees the malformed value in the swap-failed log.
        sub, app_state = _make_subscriber(
            {
                ("api", "per_op_rate_limit_enabled"): "true",
                (
                    "api",
                    "per_op_rate_limit_overrides",
                ): '{"agents.create": [1.9, 60]}',
            }
        )
        with pytest.raises(TypeError, match="must be an integer"):
            await sub.on_settings_changed("api", "per_op_rate_limit_overrides")
        app_state.per_op_limits.swap_rate_limit_config.assert_not_called()

    async def test_bool_override_rejected(self) -> None:
        # ``True`` is a subclass of ``int`` in Python and would
        # collapse to ``1`` under blind ``int(...)``.  The strict
        # validator rejects it so the operator's intent is explicit.
        sub, app_state = _make_subscriber(
            {
                ("api", "per_op_concurrency_enabled"): "true",
                (
                    "api",
                    "per_op_concurrency_overrides",
                ): '{"memory.fine_tune": true}',
            }
        )
        with pytest.raises(TypeError, match="must be an integer"):
            await sub.on_settings_changed("api", "per_op_concurrency_overrides")
        app_state.per_op_limits.swap_concurrency_config.assert_not_called()

    async def test_digit_string_override_accepted(self) -> None:
        # Operators who wrote ``"5"`` (JSON string of a whole number)
        # instead of ``5`` still get a valid update -- the strict
        # validator only rejects non-integers, not integer-shaped
        # strings.
        sub, app_state = _make_subscriber(
            {
                ("api", "per_op_concurrency_enabled"): "true",
                (
                    "api",
                    "per_op_concurrency_overrides",
                ): '{"memory.fine_tune": "5"}',
            }
        )
        await sub.on_settings_changed("api", "per_op_concurrency_overrides")
        swapped = app_state.per_op_limits.swap_concurrency_config.call_args[0][0]
        assert swapped.overrides == {"memory.fine_tune": 5}
