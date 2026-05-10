"""Tests for ObservabilitySettingsSubscriber."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.observability_subscriber import (
    ObservabilitySettingsSubscriber,
)


def _make_subscriber(
    *,
    root_log_level: str = "debug",
    enable_correlation: str = "true",
    sink_overrides: str = "{}",
    custom_sinks: str = "[]",
) -> tuple[ObservabilitySettingsSubscriber, MagicMock]:
    """Create a subscriber with a mock SettingsService.

    Returns:
        Tuple of (subscriber, mock_settings_service).
    """
    settings_service = MagicMock(spec=SettingsService)

    async def _mock_get(namespace: str, key: str) -> MagicMock:
        result = MagicMock()
        values = {
            "root_log_level": root_log_level,
            "enable_correlation": enable_correlation,
            "sink_overrides": sink_overrides,
            "custom_sinks": custom_sinks,
        }
        result.value = values.get(key, "")
        return result

    settings_service.get = AsyncMock(side_effect=_mock_get)

    sub = ObservabilitySettingsSubscriber(
        settings_service=settings_service,
        log_dir="logs",
    )
    return sub, settings_service


# ── Protocol conformance ─────────────────────────────────────────


@pytest.mark.unit
class TestObservabilitySubscriberProtocol:
    """ObservabilitySettingsSubscriber conforms to SettingsSubscriber."""

    def test_isinstance_check(self) -> None:
        sub, _ = _make_subscriber()
        assert isinstance(sub, SettingsSubscriber)

    def test_watched_keys_returns_expected_frozenset(self) -> None:
        sub, _ = _make_subscriber()
        expected = frozenset(
            {
                ("observability", "root_log_level"),
                ("observability", "enable_correlation"),
                ("observability", "sink_overrides"),
                ("observability", "custom_sinks"),
            }
        )
        assert sub.watched_keys == expected

    def test_subscriber_name(self) -> None:
        sub, _ = _make_subscriber()
        assert sub.subscriber_name == "observability-settings"


# ── Pipeline rebuild on changes ──────────────────────────────────


@pytest.mark.unit
class TestObservabilitySubscriberRebuild:
    """on_settings_changed rebuilds the logging pipeline."""

    @pytest.mark.parametrize(
        "key",
        [
            "root_log_level",
            "enable_correlation",
            "sink_overrides",
            "custom_sinks",
        ],
    )
    async def test_rebuilds_pipeline_on_any_watched_key(
        self,
        key: str,
    ) -> None:
        sub, _ = _make_subscriber()
        with patch(
            "synthorg.settings.subscribers.observability_subscriber.configure_logging",
        ) as mock_configure:
            await sub.on_settings_changed("observability", key)
            mock_configure.assert_called_once()

    async def test_passes_correct_root_level(self) -> None:
        sub, _ = _make_subscriber(root_log_level="warning")
        with patch(
            "synthorg.settings.subscribers.observability_subscriber.configure_logging",
        ) as mock_configure:
            await sub.on_settings_changed("observability", "root_log_level")
            call_kwargs = mock_configure.call_args
            config = call_kwargs[0][0]
            assert config.root_level.value == "WARNING"

    async def test_passes_correct_enable_correlation(self) -> None:
        sub, _ = _make_subscriber(enable_correlation="false")
        with patch(
            "synthorg.settings.subscribers.observability_subscriber.configure_logging",
        ) as mock_configure:
            await sub.on_settings_changed(
                "observability",
                "enable_correlation",
            )
            config = mock_configure.call_args[0][0]
            assert config.enable_correlation is False

    async def test_passes_routing_overrides_for_custom_sinks(self) -> None:
        custom = (
            '[{"file_path": "custom.log", "routing_prefixes": ["synthorg.tools."]}]'
        )
        sub, _ = _make_subscriber(custom_sinks=custom)
        with patch(
            "synthorg.settings.subscribers.observability_subscriber.configure_logging",
        ) as mock_configure:
            await sub.on_settings_changed("observability", "custom_sinks")
            call_kwargs = mock_configure.call_args
            routing = call_kwargs[1]["routing_overrides"]
            assert "custom.log" in routing
            assert routing["custom.log"] == ("synthorg.tools.",)


# ── Error handling ───────────────────────────────────────────────


@pytest.mark.unit
class TestObservabilitySubscriberErrorHandling:
    """Error handling preserves existing config."""

    async def test_settings_read_failure_preserves_config(self) -> None:
        sub, settings_service = _make_subscriber()
        settings_service.get = AsyncMock(
            side_effect=RuntimeError("DB unavailable"),
        )
        with patch(
            "synthorg.settings.subscribers.observability_subscriber.configure_logging",
        ) as mock_configure:
            # Should not raise -- error is caught internally
            await sub.on_settings_changed("observability", "sink_overrides")
            mock_configure.assert_not_called()

    async def test_validation_failure_preserves_config(self) -> None:
        sub, _ = _make_subscriber(sink_overrides="not-valid-json")
        with patch(
            "synthorg.settings.subscribers.observability_subscriber.configure_logging",
        ) as mock_configure:
            await sub.on_settings_changed("observability", "sink_overrides")
            mock_configure.assert_not_called()

    async def test_configure_logging_failure_does_not_raise(self) -> None:
        sub, _ = _make_subscriber()
        with patch(
            "synthorg.settings.subscribers.observability_subscriber.configure_logging",
            side_effect=RuntimeError("Critical sink failed"),
        ):
            # Should not raise -- error is caught internally
            await sub.on_settings_changed("observability", "root_log_level")

    async def test_invalid_root_level_preserves_config(self) -> None:
        sub, _ = _make_subscriber(root_log_level="verbose")
        with patch(
            "synthorg.settings.subscribers.observability_subscriber.configure_logging",
        ) as mock_configure:
            await sub.on_settings_changed("observability", "root_log_level")
            mock_configure.assert_not_called()


# ── Namespace guard ──────────────────────────────────────────────


@pytest.mark.unit
class TestObservabilitySubscriberNamespaceGuard:
    """Ignores unexpected namespaces."""

    async def test_ignores_unexpected_namespace(self) -> None:
        sub, settings_service = _make_subscriber()
        with patch(
            "synthorg.settings.subscribers.observability_subscriber.configure_logging",
        ) as mock_configure:
            await sub.on_settings_changed("budget", "total_monthly")
            mock_configure.assert_not_called()
            # Should not have read any settings
            settings_service.get.assert_not_awaited()


# ── Idempotency ──────────────────────────────────────────────────


@pytest.mark.unit
class TestObservabilitySubscriberIdempotency:
    """Calling on_settings_changed multiple times is safe."""

    async def test_idempotent_repeated_calls(self) -> None:
        sub, _ = _make_subscriber()
        with patch(
            "synthorg.settings.subscribers.observability_subscriber.configure_logging",
        ) as mock_configure:
            await sub.on_settings_changed("observability", "sink_overrides")
            await sub.on_settings_changed("observability", "sink_overrides")
            assert mock_configure.call_count == 2


# -- enable_correlation validation ---------------------------------


@pytest.mark.unit
class TestObservabilitySubscriberCorrelationValidation:
    """Invalid enable_correlation values are rejected."""

    @pytest.mark.parametrize(
        "value",
        ["yes", "1", "on", "banana", ""],
    )
    async def test_invalid_correlation_value_preserves_config(
        self,
        value: str,
    ) -> None:
        sub, _ = _make_subscriber(enable_correlation=value)
        with patch(
            "synthorg.settings.subscribers.observability_subscriber.configure_logging",
        ) as mock_configure:
            await sub.on_settings_changed("observability", "enable_correlation")
            mock_configure.assert_not_called()

    async def test_true_string_accepted(self) -> None:
        sub, _ = _make_subscriber(enable_correlation="true")
        with patch(
            "synthorg.settings.subscribers.observability_subscriber.configure_logging",
        ) as mock_configure:
            await sub.on_settings_changed("observability", "enable_correlation")
            config = mock_configure.call_args[0][0]
            assert config.enable_correlation is True

    async def test_false_string_accepted(self) -> None:
        sub, _ = _make_subscriber(enable_correlation="false")
        with patch(
            "synthorg.settings.subscribers.observability_subscriber.configure_logging",
        ) as mock_configure:
            await sub.on_settings_changed("observability", "enable_correlation")
            config = mock_configure.call_args[0][0]
            assert config.enable_correlation is False


# -- MemoryError/RecursionError re-raise ---------------------------


@pytest.mark.unit
class TestObservabilitySubscriberFatalErrors:
    """MemoryError and RecursionError propagate through the subscriber."""

    async def test_memory_error_from_settings_read_propagates(self) -> None:
        sub, settings_service = _make_subscriber()
        settings_service.get = AsyncMock(side_effect=MemoryError)
        with pytest.raises(MemoryError):
            await sub.on_settings_changed("observability", "sink_overrides")

    async def test_recursion_error_from_build_propagates(self) -> None:
        sub, _ = _make_subscriber()
        with (
            patch(
                "synthorg.settings.subscribers.observability_subscriber"
                ".build_log_config_from_settings",
                side_effect=RecursionError,
            ),
            pytest.raises(RecursionError),
        ):
            await sub.on_settings_changed("observability", "sink_overrides")

    async def test_memory_error_from_configure_propagates(self) -> None:
        sub, _ = _make_subscriber()
        with (
            patch(
                "synthorg.settings.subscribers.observability_subscriber"
                ".configure_logging",
                side_effect=MemoryError,
            ),
            pytest.raises(MemoryError),
        ):
            await sub.on_settings_changed("observability", "sink_overrides")


# -- Rebuild lock --------------------------------------------------


@pytest.mark.unit
class TestObservabilitySubscriberLock:
    """Subscriber has a rebuild lock for serialization."""

    def test_has_rebuild_lock(self) -> None:
        import asyncio

        sub, _ = _make_subscriber()
        assert isinstance(sub._rebuild_lock, asyncio.Lock)
