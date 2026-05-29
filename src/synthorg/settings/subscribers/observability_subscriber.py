"""Observability settings subscriber -- reconfigure log pipeline at runtime."""

import asyncio
import sys
from typing import TYPE_CHECKING, Any

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.normalization import normalize_ascii_lowercase
from synthorg.observability import get_logger
from synthorg.observability.enums import LogLevel
from synthorg.observability.events.settings import (
    SETTINGS_OBSERVABILITY_PIPELINE_REBUILT,
    SETTINGS_OBSERVABILITY_REBUILD_FAILED,
    SETTINGS_OBSERVABILITY_VALIDATION_FAILED,
    SETTINGS_SUBSCRIBER_NOTIFIED,
)
from synthorg.observability.setup import configure_logging
from synthorg.observability.sink_config_builder import (
    SinkBuildResult,
    build_log_config_from_settings,
)

if TYPE_CHECKING:
    from synthorg.settings.service import SettingsService

logger = get_logger(__name__)

_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {
        ("observability", "root_log_level"),
        ("observability", "enable_correlation"),
        ("observability", "sink_overrides"),
        ("observability", "custom_sinks"),
    }
)

_VALID_BOOL_STRINGS: frozenset[str] = frozenset({"true", "false"})


class ObservabilitySettingsSubscriber:
    """React to observability-namespace settings changes.

    Any watched key change triggers a full logging pipeline rebuild
    via :func:`configure_logging`.  The subscriber reads all current
    settings, merges defaults with overrides, and reconfigures.

    On settings-read or validation failure, the existing logging
    configuration is preserved.  On ``configure_logging`` failure,
    the pipeline may be in a degraded state because old handlers are
    torn down before new ones are attached (not atomic).

    Rapid successive changes are serialized by an ``asyncio.Lock``
    so the final configuration reflects the last completed rebuild
    (last-write-wins).

    Args:
        settings_service: Settings service for reading current values.
        log_dir: Log file directory (fixed at construction time).
    """

    def __init__(
        self,
        settings_service: SettingsService,
        log_dir: str = "logs",
    ) -> None:
        self._settings_service = settings_service
        self._log_dir = log_dir
        self._rebuild_lock = asyncio.Lock()

    @property
    def watched_keys(self) -> frozenset[tuple[str, str]]:
        """Return observability-namespace keys this subscriber watches."""
        return _WATCHED

    @property
    def subscriber_name(self) -> str:
        """Human-readable subscriber name."""
        return "observability-settings"

    async def on_settings_changed(
        self,
        namespace: str,
        key: str,
    ) -> None:
        """Handle an observability setting change.

        Acquires the rebuild lock to serialize concurrent rebuilds,
        then delegates to :meth:`_rebuild_pipeline`.

        Args:
            namespace: Changed setting namespace.
            key: Changed setting key.
        """
        if namespace != "observability":
            logger.warning(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace=namespace,
                key=key,
                note="ignored unexpected namespace",
            )
            return

        async with self._rebuild_lock:
            await self._rebuild_pipeline(key)

    async def _read_all_settings(self) -> tuple[Any, ...]:
        """Read all 4 observability settings in parallel.

        Returns:
            A 4-tuple of resolved results for ``root_log_level``,
            ``enable_correlation``, ``sink_overrides`` and
            ``custom_sinks`` (in that order).
        """
        return await asyncio.gather(
            self._settings_service.get("observability", "root_log_level"),
            self._settings_service.get(
                "observability",
                "enable_correlation",
            ),
            self._settings_service.get("observability", "sink_overrides"),
            self._settings_service.get("observability", "custom_sinks"),
        )

    def _parse_and_build(
        self,
        results: tuple[Any, ...],
        key: str,
    ) -> SinkBuildResult | None:
        """Parse settings and build log config.

        Returns:
            A :class:`SinkBuildResult` with the rebuilt log configuration,
            or ``None`` when any setting value fails validation (the
            failure is logged and the existing config is kept).
        """
        root_result, corr_result, over_result, cust_result = results

        try:
            root_level = LogLevel(root_result.value.upper())
        except ValueError, AttributeError:
            logger.error(
                SETTINGS_OBSERVABILITY_VALIDATION_FAILED,
                subscriber=self.subscriber_name,
                key=key,
                note=f"invalid root_log_level: {root_result.value!r}",
            )
            return None

        raw_corr = normalize_ascii_lowercase(str(corr_result.value))
        if raw_corr not in _VALID_BOOL_STRINGS:
            logger.error(
                SETTINGS_OBSERVABILITY_VALIDATION_FAILED,
                subscriber=self.subscriber_name,
                key=key,
                note=f"invalid enable_correlation: {corr_result.value!r}",
            )
            return None
        enable_correlation = raw_corr == "true"

        try:
            return build_log_config_from_settings(
                root_level=root_level,
                enable_correlation=enable_correlation,
                sink_overrides_json=over_result.value,
                custom_sinks_json=cust_result.value,
                log_dir=self._log_dir,
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.error(
                SETTINGS_OBSERVABILITY_VALIDATION_FAILED,
                subscriber=self.subscriber_name,
                key=key,
                note="invalid sink configuration -- keeping existing config",
            )
            return None

    def _apply_config(
        self,
        build_result: SinkBuildResult,
        key: str,
    ) -> None:
        """Call ``configure_logging`` with stderr fallback on failure."""
        routing = build_result.routing_overrides or None
        try:
            configure_logging(
                build_result.config,
                routing_overrides=routing,
            )
        except Exception as exc:
            reraise_critical(exc)
            # Pipeline may be degraded -- stderr as fallback.
            sys.stderr.write(
                f"WARNING: configure_logging failed during hot reload "
                f"for key={key!r}; logging may be degraded\n",
            )
            sys.stderr.flush()
            logger.error(
                SETTINGS_OBSERVABILITY_REBUILD_FAILED,
                subscriber=self.subscriber_name,
                key=key,
                note=(
                    "configure_logging failed -- old pipeline was already "
                    "torn down; logging may be degraded"
                ),
            )
            return

        logger.info(
            SETTINGS_OBSERVABILITY_PIPELINE_REBUILT,
            subscriber=self.subscriber_name,
            key=key,
            sink_count=len(build_result.config.sinks),
            custom_routing_count=len(build_result.routing_overrides),
        )

    async def _rebuild_pipeline(self, key: str) -> None:
        """Full pipeline rebuild: read, parse, build, apply."""
        try:
            results = await self._read_all_settings()
        except Exception as exc:
            reraise_critical(exc)
            logger.error(
                SETTINGS_OBSERVABILITY_REBUILD_FAILED,
                subscriber=self.subscriber_name,
                key=key,
                note="failed to read settings",
            )
            return

        build_result = self._parse_and_build(results, key)
        if build_result is None:
            return

        self._apply_config(build_result, key)
