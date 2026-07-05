"""Security invariant: the self-modifying switch stays restart-bound.

``self_improvement.code_modification_enabled`` (self-modifying code, GitHub
credentials validated only at startup) must remain ``restart_required=True`` and
must not be watched by any registered settings subscriber, so no live settings
write can enable it. A careless future flip fails this test.

``chief_of_staff.direct_mcp_enabled`` is deliberately NOT in that set: it is
hot-reloadable, but stays fail-closed a different way. Its actor is rebuilt on
every toggle through the same governance gate the startup wirer uses
(:func:`build_conversational_actor`), so a live enable materialises the actor
only when security governance + the MCP self-consumer are wired on the boot
engine. The security property is *fail-closed*, not *restart-bound*: the second
half of this module asserts that a re-checking subscriber watches it and that it
is no longer restart-required.
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

_KEEP_KEYS: tuple[tuple[str, str], ...] = (
    ("self_improvement", "code_modification_enabled"),
)

_DIRECT_MCP: tuple[str, str] = ("chief_of_staff", "direct_mcp_enabled")


async def _build_dispatcher(
    settings: SettingsService,
) -> SettingsChangeDispatcher | None:
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


@pytest.mark.parametrize(("namespace", "key"), _KEEP_KEYS)
def test_keep_settings_remain_restart_required(namespace: str, key: str) -> None:
    defn = get_registry().get(namespace, key)
    assert defn is not None
    assert defn.restart_required is True


@pytest.mark.parametrize(("namespace", "key"), _KEEP_KEYS)
async def test_no_registered_subscriber_watches_keep_settings(
    namespace: str, key: str
) -> None:
    """No registered settings subscriber reacts to a KEEP-setting change.

    Enumerates the dispatcher's whole subscriber roster from the real
    registration path (not a hand-picked subset), so a future subscriber that
    starts watching a KEEP key is caught here. The dispatcher also skips
    restart-required settings outright, so a runtime write is never delivered;
    this guards the second line of defence (a subscriber must never opt one of
    these into live reconciliation). Optional subscribers are wired by passing
    a backup service + approval-timeout scheduler so the roster is exhaustive.
    """
    backend = FakePersistenceBackend()
    await backend.connect()
    try:
        settings = SettingsService(repository=backend.settings, registry=get_registry())
        dispatcher = await _build_dispatcher(settings)
        assert dispatcher is not None
        for sub in dispatcher.subscribers:
            assert (namespace, key) not in sub.watched_keys
    finally:
        await backend.disconnect()


def test_direct_mcp_is_hot_reloadable_not_restart_bound() -> None:
    """Direct-MCP acting hot-reloads: it is no longer restart-required.

    The fail-closed guarantee moved from a restart bind to a per-rebuild
    governance re-check, so the setting must NOT carry restart_required.
    """
    defn = get_registry().get(*_DIRECT_MCP)
    assert defn is not None
    assert defn.restart_required is False


async def test_a_rechecking_subscriber_watches_direct_mcp() -> None:
    """Exactly the direct-MCP actor subscriber watches direct_mcp_enabled.

    A live toggle must reach a subscriber that rebuilds the actor through the
    fail-closed gate; if it watched nothing, enabling it would silently need a
    restart again.
    """
    backend = FakePersistenceBackend()
    await backend.connect()
    try:
        settings = SettingsService(repository=backend.settings, registry=get_registry())
        dispatcher = await _build_dispatcher(settings)
        assert dispatcher is not None
        watchers = [
            sub for sub in dispatcher.subscribers if _DIRECT_MCP in sub.watched_keys
        ]
        names = {sub.subscriber_name for sub in watchers}
        assert "direct-mcp-actor-settings" in names
    finally:
        await backend.disconnect()
