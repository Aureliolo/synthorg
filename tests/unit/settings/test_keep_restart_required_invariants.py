"""Security invariant: the two acting/self-modifying switches stay restart-bound.

``chief_of_staff.direct_mcp_enabled`` (autonomous MCP acting, fail-closed at
construction) and ``self_improvement.code_modification_enabled`` (self-modifying
code, GitHub credentials validated only at startup) must remain
``restart_required=True`` and must not be watched by any registered settings
subscriber, so no live settings write can enable them. A careless future flip
fails this test.
"""

import pytest

from synthorg.api.lifecycle_helpers.settings_dispatcher import (
    _build_settings_dispatcher,
)
from synthorg.backup.service import BackupService
from synthorg.communication.bus_protocol import MessageBus
from synthorg.config.schema import RootConfig
from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler
from synthorg.settings.registry import get_registry
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.settings.state import SettingsStateSlice
from tests._shared import make_app_state, mock_of
from tests.unit.api.fakes import FakePersistenceBackend

pytestmark = pytest.mark.unit

_KEEP_KEYS: tuple[tuple[str, str], ...] = (
    ("chief_of_staff", "direct_mcp_enabled"),
    ("self_improvement", "code_modification_enabled"),
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
        resolver = ConfigResolver(
            settings_service=settings, config=RootConfig(company_name="test")
        )
        app_state = make_app_state(
            slices={SettingsStateSlice: {"config_resolver": resolver}}
        )
        dispatcher = _build_settings_dispatcher(
            message_bus=mock_of[MessageBus](),
            settings_service=settings,
            config=RootConfig(company_name="test"),
            app_state=app_state,
            backup_service=mock_of[BackupService](),
            approval_timeout_scheduler=mock_of[ApprovalTimeoutScheduler](),
        )
        assert dispatcher is not None
        for sub in dispatcher.subscribers:
            assert (namespace, key) not in sub.watched_keys
    finally:
        await backend.disconnect()
