# module-kind: code
"""Periodic driver for full audit-chain verification.

``AsyncCycleScheduler`` runs its first cycle eagerly, on ``start()``,
before any wait -- so a scheduler started immediately after
``AuditChainSink.attach_persistence`` returns performs the boot-time
verification itself; that structural property is why the sink does not
also verify explicitly. This scheduler then re-runs the same full walk
(hash continuity plus every entry's signature) on a cadence, so the
documented ``synthorg_audit_chain_verifications_total{outcome="broken"}``
alert stays reachable for the life of the process, not only at restart.

Settings-layer imports are deferred to ``_resolve_wait_interval`` rather
than module level, matching ``factory.py``'s ``_resolve_preset_urls``: the
audit-chain package is wired during ``configure_logging``, before the
settings/API layers exist, so a module-level import back into them would
risk the exact import-time cycle those call sites already route around.
"""

from typing import TYPE_CHECKING, Final, override

from synthorg.core.scheduler import AsyncCycleScheduler
from synthorg.observability import get_logger
from synthorg.observability.audit_chain.sink import AuditChainSink
from synthorg.observability.events.audit_chain import (
    AUDIT_CHAIN_PERSIST_INTEGRITY_FAILED,
    AUDIT_CHAIN_VERIFY_SCHEDULER_FAILED,
    AUDIT_CHAIN_VERIFY_SCHEDULER_STARTED,
    AUDIT_CHAIN_VERIFY_SCHEDULER_STOPPED,
)

if TYPE_CHECKING:
    from synthorg.api.state_slices import AppStateSliceMixin

logger = get_logger(__name__)

_INTERVAL_KEY: Final[str] = "audit_chain_verify_interval_seconds"

DEFAULT_VERIFY_INTERVAL_SECONDS: Final[float] = 3600.0
"""Fallback cadence when no settings resolver is wired in yet.

Mirrors the registered ``observability.audit_chain_verify_interval_seconds``
default; the boot wiring passes this as the scheduler's starting interval
before a resolver exists to read the operator's own value from.
"""


class AuditChainVerificationScheduler(AsyncCycleScheduler):
    """Re-verifies the live audit chain on a cadence.

    Args:
        sink: The live ``AuditChainSink`` to re-verify each cycle.
        app_state: Application state (any slice-reader), re-read each
            cycle for the live cadence setting.
        interval_seconds: Starting cadence; re-resolved per cycle so an
            operator change applies without a restart.
    """

    def __init__(
        self,
        sink: AuditChainSink,
        app_state: AppStateSliceMixin,
        *,
        interval_seconds: float,
    ) -> None:
        super().__init__(
            interval_seconds=interval_seconds,
            task_name="audit-chain-verify",
            started_event=AUDIT_CHAIN_VERIFY_SCHEDULER_STARTED,
            stopped_event=AUDIT_CHAIN_VERIFY_SCHEDULER_STOPPED,
            failed_event=AUDIT_CHAIN_VERIFY_SCHEDULER_FAILED,
        )
        self._sink = sink
        self._app_state = app_state
        self._boot_cycle_done = False

    @override
    async def _run_cycle_once(self) -> None:
        """Verify the live chain and warn on a break; the metric always fires.

        The verifier's own ``verify_chain`` records
        ``synthorg_audit_chain_verifications_total{outcome}`` on every call
        regardless of outcome; this only adds the durability-side warning a
        broken re-verification deserves. ``_run`` runs this eagerly on
        ``start()``, before any wait, so the FIRST call is the boot-time
        check the sink deliberately no longer performs itself; every call
        after that is a genuine periodic re-verify.
        """
        trigger = "periodic_verify" if self._boot_cycle_done else "boot_verify"
        self._boot_cycle_done = True
        result = await self._sink.verify_chain()
        if not result.valid:
            logger.warning(
                AUDIT_CHAIN_PERSIST_INTEGRITY_FAILED,
                entries_checked=result.entries_checked,
                first_break_position=result.first_break_position,
                trigger=trigger,
            )

    @override
    async def _resolve_wait_interval(self) -> float:
        """Re-read the cadence so a change applies without a restart.

        Returns:
            The resolved interval, or the boot value when no resolver is
            wired yet.
        """
        from synthorg.settings.enums import SettingNamespace  # noqa: PLC0415
        from synthorg.settings.state import (  # noqa: PLC0415
            SettingsStateSlice,
            config_resolver_of,
        )

        if self._app_state.slice(SettingsStateSlice).config_resolver is None:
            return self._interval
        return await config_resolver_of(self._app_state).get_float(
            SettingNamespace.OBSERVABILITY.value,
            _INTERVAL_KEY,
        )


__all__ = ["AuditChainVerificationScheduler"]
