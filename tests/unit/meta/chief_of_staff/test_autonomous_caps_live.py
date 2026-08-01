"""Live-gate coverage for the autonomous Chief-of-Staff capabilities.

``narrative_enabled`` / ``invite_enabled`` / ``routing_enabled`` are each
gated per run/turn by the persona master switch
(``self_improvement.chief_of_staff_enabled``) AND the per-capability flag,
resolved live through ``resolve_cos_autonomous_cap``. A settings write takes
effect on the next call with no restart, exactly like the already-hot
``propose_enabled`` sibling. On a resolver outage the gate falls back to the
baked master, so a disabled persona cannot resume spending/acting.
"""

from collections.abc import AsyncIterator

import pytest

from synthorg.config.schema import RootConfig
from synthorg.docs_engine.service import DocsService
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.narrative.reader import NarrativeReader
from synthorg.meta.chief_of_staff.narrative.service import ChiefOfStaffNarrator
from synthorg.meta.chief_of_staff.narrative.synthesiser import NarrativeSynthesiser
from synthorg.settings.registry import get_registry
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from tests._shared import mock_of
from tests._shared.scripted_provider import ScriptedProvider
from tests.unit.api.fakes import FakePersistenceBackend
from tests.unit.meta.chief_of_staff.group_chat_fakes import (
    ScriptedAgentCaller,
    build_group_chat_with_invites,
)
from tests.unit.meta.chief_of_staff.propose_fakes import (
    build_proposer,
    build_registry,
    make_identity,
)

pytestmark = pytest.mark.unit


@pytest.fixture
async def settings() -> AsyncIterator[SettingsService]:
    backend = FakePersistenceBackend()
    await backend.connect()
    yield SettingsService(repository=backend.settings, registry=get_registry())
    await backend.disconnect()


def _resolver(settings: SettingsService) -> ConfigResolver:
    return ConfigResolver(
        settings_service=settings, config=RootConfig(company_name="test")
    )


async def _set_master(settings: SettingsService, *, on: bool) -> None:
    await settings.set(
        "self_improvement", "chief_of_staff_enabled", "true" if on else "false"
    )


def _narrator(
    *,
    config: ChiefOfStaffConfig,
    config_resolver: ConfigResolver | None,
    master_enabled: bool = True,
) -> ChiefOfStaffNarrator:
    """Build a narrator whose gate deps are real and the rest stubbed."""
    return ChiefOfStaffNarrator(
        reader=mock_of[NarrativeReader](),
        synthesiser=mock_of[NarrativeSynthesiser](),
        docs=mock_of[DocsService](),
        config=config,
        config_resolver=config_resolver,
        master_enabled=master_enabled,
    )


async def test_routing_gate_is_live_and_master_gated(
    settings: SettingsService,
) -> None:
    """``routing_enabled`` flips the per-turn routing gate live + master-gated."""
    proposer, *_ = build_proposer(
        provider=ScriptedProvider(responses=[]),
        config=ChiefOfStaffConfig(propose_enabled=True),
        config_resolver=_resolver(settings),
    )
    await _set_master(settings, on=True)

    # The live read tracks the settings store, not the baked config, so set
    # the cap explicitly rather than relying on its registry default.
    await settings.set("chief_of_staff", "routing_enabled", "false")
    assert await proposer._routing_enabled() is False
    await settings.set("chief_of_staff", "routing_enabled", "true")
    assert await proposer._routing_enabled() is True

    # Master off suspends the cap even with its own flag still on.
    await _set_master(settings, on=False)
    assert await proposer._routing_enabled() is False


async def test_narrative_gate_is_live_and_master_gated(
    settings: SettingsService,
) -> None:
    """``narrative_enabled`` flips the per-run narrative gate live + master-gated."""
    narrator = _narrator(
        config=ChiefOfStaffConfig(narrative_enabled=False),
        config_resolver=_resolver(settings),
    )
    await _set_master(settings, on=True)

    assert await narrator._narrative_active() is False
    await settings.set("chief_of_staff", "narrative_enabled", "true")
    assert await narrator._narrative_active() is True

    await _set_master(settings, on=False)
    assert await narrator._narrative_active() is False


async def test_invite_gate_is_live_and_master_gated(
    settings: SettingsService,
) -> None:
    """``invite_enabled`` flips the per-round invite coordinator live + master."""
    registry = await build_registry(make_identity(name="Dana", role="CEO"))
    service, *_ = build_group_chat_with_invites(
        agent_caller=ScriptedAgentCaller({}),
        registry=registry,
        config=ChiefOfStaffConfig(group_chat_enabled=True, invite_enabled=False),
        config_resolver=_resolver(settings),
    )
    await _set_master(settings, on=True)

    assert await service._live_invite_coordinator() is None
    await settings.set("chief_of_staff", "invite_enabled", "true")
    assert await service._live_invite_coordinator() is not None

    await _set_master(settings, on=False)
    assert await service._live_invite_coordinator() is None


async def test_master_fallback_fails_safe_on_resolver_outage() -> None:
    """With no resolver, the gate falls back to the baked master, not ``True``.

    A disabled persona (``master_enabled=False``) must keep an autonomous cap
    off even when the cap's own baked flag is on, so a settings outage cannot
    resume narrative spend after the operator turned the persona off.
    """
    off = _narrator(
        config=ChiefOfStaffConfig(narrative_enabled=True),
        config_resolver=None,
        master_enabled=False,
    )
    assert await off._narrative_active() is False

    on = _narrator(
        config=ChiefOfStaffConfig(narrative_enabled=True),
        config_resolver=None,
        master_enabled=True,
    )
    assert await on._narrative_active() is True


async def test_off_by_default_cap_toggles_live_like_propose(
    settings: SettingsService,
) -> None:
    """Parity: an off-by-default cap is as hot as ``propose_enabled``.

    Both are live (``compose_set=False``) and both take effect on the next
    call. ``narrative_enabled`` stands in for the autonomous caps.
    """
    registry = get_registry()
    propose_defn = registry.get("chief_of_staff", "propose_enabled")
    narrative_defn = registry.get("chief_of_staff", "narrative_enabled")
    assert propose_defn is not None
    assert narrative_defn is not None
    assert propose_defn.compose_set is False
    assert narrative_defn.compose_set is False

    narrator = _narrator(
        config=ChiefOfStaffConfig(narrative_enabled=False),
        config_resolver=_resolver(settings),
    )
    await _set_master(settings, on=True)

    assert await narrator._narrative_active() is False
    await settings.set("chief_of_staff", "narrative_enabled", "true")
    assert await narrator._narrative_active() is True
    await settings.set("chief_of_staff", "narrative_enabled", "false")
    assert await narrator._narrative_active() is False
