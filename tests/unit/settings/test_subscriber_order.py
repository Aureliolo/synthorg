"""Registration order where one subscriber has to observe another's result.

The dispatcher notifies subscribers sequentially in registration order. That
is an implementation detail everywhere except here: two subscribers watch the
memory keys, and the second reads what the first installed.
"""

import pytest

from synthorg.api.lifecycle_helpers.settings_dispatcher import (
    _build_settings_dispatcher,
)
from synthorg.backup.service import BackupService
from synthorg.communication.bus_protocol import MessageBus
from synthorg.config.schema import RootConfig
from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler
from synthorg.settings.dispatcher import SettingsChangeDispatcher
from synthorg.settings.registry import get_registry
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.settings.state import SettingsStateSlice
from tests._shared import make_app_state, mock_of
from tests.unit.api.fakes import FakePersistenceBackend

pytestmark = pytest.mark.unit

_MEMORY_BACKEND_KEY: tuple[str, str] = ("memory", "backend")


async def _build_dispatcher(
    settings: SettingsService,
) -> SettingsChangeDispatcher | None:
    """Build the shipped subscriber roster.

    Returns:
        The dispatcher, or ``None`` when composition declines to build one.
    """
    app_state = make_app_state(
        slices={
            SettingsStateSlice: {
                "config_resolver": ConfigResolver(
                    settings_service=settings,
                    config=RootConfig(company_name="test"),
                )
            }
        }
    )
    return _build_settings_dispatcher(
        message_bus=mock_of[MessageBus](),
        settings_service=settings,
        config=RootConfig(company_name="test"),
        app_state=app_state,
        backup_service=mock_of[BackupService](),
        approval_timeout_scheduler=mock_of[ApprovalTimeoutScheduler](),
    )


async def test_the_reconciler_runs_before_the_runtime_reload() -> None:
    """The engine must be rebuilt against the backend the reconciler installed.

    Both subscribers watch ``memory.backend``. The reconciler replaces the
    backend instance; the runtime reload captures whatever is in the slice by
    value. Reloading first would hand every agent the instance the reconciler
    is about to disconnect, and nothing would trigger a second pass to correct
    it.
    """
    backend = FakePersistenceBackend()
    await backend.connect()
    try:
        settings = SettingsService(repository=backend.settings, registry=get_registry())
        dispatcher = await _build_dispatcher(settings)
        assert dispatcher is not None
        watchers = [
            sub.subscriber_name
            for sub in dispatcher.subscribers
            if _MEMORY_BACKEND_KEY in sub.watched_keys
        ]
        assert "subsystem-reconcile" in watchers
        assert "runtime-reload" in watchers
        assert watchers.index("subsystem-reconcile") < watchers.index("runtime-reload")
    finally:
        await backend.disconnect()


async def test_each_subscriber_is_registered_once() -> None:
    """A subscriber registered twice would run its side effect twice."""
    backend = FakePersistenceBackend()
    await backend.connect()
    try:
        settings = SettingsService(repository=backend.settings, registry=get_registry())
        dispatcher = await _build_dispatcher(settings)
        assert dispatcher is not None
        names = [sub.subscriber_name for sub in dispatcher.subscribers]
        assert len(names) == len(set(names))
    finally:
        await backend.disconnect()
