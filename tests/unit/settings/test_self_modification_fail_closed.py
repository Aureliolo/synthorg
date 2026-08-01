"""Security invariant: self-modifying code cannot be enabled by a flag alone.

``self_improvement.code_modification_enabled`` is a live setting, so an
operator can turn it on from the dashboard. Two things keep that safe. The
write itself is a security-weakening transition, so it needs the deliberate
confirm + reason + actor rather than an ordinary PUT. And the credential
requirement is independent of the write: without a GitHub token and repo in
the ``meta.self_improvement`` blob the config refuses to validate and the load
path forces the flag back off, so the loop never runs with the switch on and
nowhere to push.

``chief_of_staff.direct_mcp_enabled`` is fail-closed the same way, through the
subsystem reconciler, which rebuilds the actor behind the governance gate on
every toggle.
"""

import json

import pytest

from synthorg.api.lifecycle_helpers.settings_dispatcher import (
    _build_settings_dispatcher,
)
from synthorg.backup.service import BackupService
from synthorg.communication.bus_protocol import MessageBus
from synthorg.config.schema import RootConfig
from synthorg.meta.config import load_self_improvement_config
from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler
from synthorg.settings.dispatcher import SettingsChangeDispatcher
from synthorg.settings.errors import SecurityToggleConfirmationRequiredError
from synthorg.settings.registry import get_registry
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.settings.state import SettingsStateSlice
from synthorg.settings.write_governance import SettingsWriteGovernance
from tests._shared import make_app_state, mock_of
from tests.unit.api.fakes import FakePersistenceBackend

pytestmark = pytest.mark.unit

_CODE_MOD: tuple[str, str] = ("self_improvement", "code_modification_enabled")
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


@pytest.mark.parametrize(("namespace", "key"), [_CODE_MOD, _DIRECT_MCP])
def test_fail_closed_switches_are_operator_changeable(namespace: str, key: str) -> None:
    """Neither switch is compose-set: an operator can turn them on live."""
    defn = get_registry().get(namespace, key)
    assert defn is not None
    assert defn.compose_set is False


async def test_enabling_code_modification_needs_the_deliberate_path() -> None:
    """An ordinary write cannot turn self-modifying code on.

    The flag stopped being restart-bound, so "you have to restart to apply
    it" is no longer what stands between an ordinary settings write and the
    loop editing its own source. The write-governance guardrail is.
    """
    backend = FakePersistenceBackend()
    await backend.connect()
    try:
        settings = SettingsService(repository=backend.settings, registry=get_registry())
        with pytest.raises(SecurityToggleConfirmationRequiredError):
            await settings.set(*_CODE_MOD, "true")
    finally:
        await backend.disconnect()


async def test_code_modification_without_credentials_loads_disabled() -> None:
    """Setting the flag with no GitHub credentials leaves it off.

    Independent of the write guardrail above: even an authorised, deliberate
    enable cannot produce a config that has code modification on and nowhere
    to push it.
    """
    backend = FakePersistenceBackend()
    await backend.connect()
    try:
        settings = SettingsService(repository=backend.settings, registry=get_registry())
        await settings.set(
            *_CODE_MOD,
            "true",
            governance=SettingsWriteGovernance(
                confirm=True, reason="test", actor="admin"
            ),
        )
        await settings.set("meta", "self_improvement", json.dumps({}))

        config = await load_self_improvement_config(settings)

        assert config.code_modification_enabled is False
    finally:
        await backend.disconnect()


async def test_no_registered_subscriber_watches_code_modification() -> None:
    """Nothing shortcuts the load path for the code-modification flag.

    Asserted across the whole roster rather than against the one subscriber
    that documents the exclusion: a new subscriber that watched the key
    would apply the flag without the credential re-read that forces it back
    off, and would do so without touching the file carrying the rationale.
    """
    backend = FakePersistenceBackend()
    await backend.connect()
    try:
        settings = SettingsService(repository=backend.settings, registry=get_registry())
        dispatcher = await _build_dispatcher(settings)
        assert dispatcher is not None
        watchers = {
            sub.subscriber_name
            for sub in dispatcher.subscribers
            if _CODE_MOD in sub.watched_keys
        }
        assert watchers == set()
    finally:
        await backend.disconnect()


async def test_the_reconciler_watches_direct_mcp() -> None:
    """The subsystem reconciler is what a direct_mcp_enabled write reaches.

    A live toggle must reach something that rebuilds the actor through the
    fail-closed gate. That is the reconciler: the key is declared on the
    ``conversational_actor`` spec, so the reconcile subscriber derives it
    rather than a second hand-kept list naming it again.
    """
    backend = FakePersistenceBackend()
    await backend.connect()
    try:
        settings = SettingsService(repository=backend.settings, registry=get_registry())
        dispatcher = await _build_dispatcher(settings)
        assert dispatcher is not None
        names = {
            sub.subscriber_name
            for sub in dispatcher.subscribers
            if _DIRECT_MCP in sub.watched_keys
        }
        assert "subsystem-reconcile" in names
        # The reconciler is the only writer of the actor slice. The other
        # watcher refreshes the self-improvement config and touches no
        # wiring; a second subscriber rebuilding the actor is the drift the
        # single-owner rule exists to stop.
        assert "direct-mcp-actor-settings" not in names
    finally:
        await backend.disconnect()
