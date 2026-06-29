"""Conformance for the feature-enablement flags + per-feature models.

Each dissolved Chief-of-Staff / self-improvement / knowledge flag (and
each per-feature model) is asserted on three axes:

1. Registry presence with the documented default + type.
2. Default resolution through ``SettingsService.get()`` (DB empty).
3. The runtime-vs-restart classification (``restart_required``).

The on-by-default posture lives in these defaults: only the four
off-categories (self-modification, autonomous spend, egress,
acts-on-your-behalf) ship ``"false"``; the conversational + knowledge
capabilities ship ``"true"``.
"""

from unittest.mock import AsyncMock

import pytest

from synthorg.persistence.settings_protocol import SettingsRepository
from synthorg.settings import definitions as _definitions  # noqa: F401
from synthorg.settings.enums import SettingSource, SettingType
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService

pytestmark = pytest.mark.unit


@pytest.fixture
def service() -> SettingsService:
    """A settings service over an empty repository (defaults resolve)."""
    repo = AsyncMock(spec=SettingsRepository)
    repo.get.return_value = None
    repo.get_namespace.return_value = ()
    repo.list_items.return_value = ()
    return SettingsService(repository=repo, registry=get_registry())


# (namespace, key, type, default, restart_required).
_ENTRIES: tuple[tuple[str, str, SettingType, str, bool], ...] = (
    # Conversational Chief-of-Staff capabilities: on, runtime-toggleable.
    # Explain / propose / group chat are live-gated at the HTTP controller
    # (built at boot on-by-default), so they toggle with no restart.
    ("chief_of_staff", "explain_chat_enabled", SettingType.BOOLEAN, "true", False),
    ("chief_of_staff", "propose_enabled", SettingType.BOOLEAN, "true", False),
    ("chief_of_staff", "group_chat_enabled", SettingType.BOOLEAN, "true", False),
    # Routing is gated live per turn in the proposer -> no restart.
    ("chief_of_staff", "routing_enabled", SettingType.BOOLEAN, "true", False),
    # Autonomous capabilities: off, now gated live (per cycle / per turn, or
    # started/stopped by a settings subscriber) -> no restart.
    ("chief_of_staff", "learning_enabled", SettingType.BOOLEAN, "false", False),
    ("chief_of_staff", "alerts_enabled", SettingType.BOOLEAN, "false", False),
    ("chief_of_staff", "narrative_enabled", SettingType.BOOLEAN, "false", False),
    # Agent invite: off, gated live per group-chat turn -> no restart.
    ("chief_of_staff", "invite_enabled", SettingType.BOOLEAN, "false", False),
    # Direct MCP acting: off, fail-closed at boot (needs security governance
    # wired at startup), so enabling it stays restart-required (KEEP).
    ("chief_of_staff", "direct_mcp_enabled", SettingType.BOOLEAN, "false", True),
    # Per-feature models: blank by default (setup auto-selects); read live
    # per LLM call -> no restart.
    ("chief_of_staff", "chat_model", SettingType.STRING, "", False),
    ("chief_of_staff", "propose_model", SettingType.STRING, "", False),
    ("chief_of_staff", "routing_model", SettingType.STRING, "", False),
    ("chief_of_staff", "narrative_model", SettingType.STRING, "", False),
    # Self-modification: every switch off (config_tuning only matters when
    # the master is on). The meta-loop re-reads them live -> no restart.
    ("self_improvement", "enabled", SettingType.BOOLEAN, "false", False),
    ("self_improvement", "chief_of_staff_enabled", SettingType.BOOLEAN, "false", False),
    ("self_improvement", "config_tuning_enabled", SettingType.BOOLEAN, "true", False),
    (
        "self_improvement",
        "architecture_proposals_enabled",
        SettingType.BOOLEAN,
        "false",
        False,
    ),
    ("self_improvement", "prompt_tuning_enabled", SettingType.BOOLEAN, "false", False),
    # Code modification validates GitHub credentials at startup, so enabling
    # self-modifying code stays restart-required (KEEP).
    (
        "self_improvement",
        "code_modification_enabled",
        SettingType.BOOLEAN,
        "false",
        True,
    ),
    ("self_improvement", "tool_creation_enabled", SettingType.BOOLEAN, "false", False),
    ("self_improvement", "analysis_model", SettingType.STRING, "", False),
    ("self_improvement", "code_modification_model", SettingType.STRING, "", False),
    # Knowledge: on by default, no model of its own; ghost-wired and
    # live-gated at the knowledge tools, so a change applies with no restart.
    ("knowledge", "enabled", SettingType.BOOLEAN, "true", False),
)


type _Entry = tuple[str, str, SettingType, str, bool]

# Readable parametrize ids ("chief_of_staff/propose_enabled") so a failing
# case names the namespace/key instead of "entry7".
_ENTRY_IDS = [f"{ns}/{key}" for ns, key, *_ in _ENTRIES]


@pytest.mark.parametrize("entry", _ENTRIES, ids=_ENTRY_IDS)
def test_entry_registered_with_default(entry: _Entry) -> None:
    """The definition exists with the documented type, default, and flag."""
    namespace, key, stype, default, restart = entry
    defn = get_registry().get(namespace, key)
    assert defn is not None, f"{namespace}/{key} not registered"
    assert defn.type is stype
    assert defn.default == default
    assert defn.restart_required is restart


@pytest.mark.parametrize("entry", _ENTRIES, ids=_ENTRY_IDS)
async def test_default_resolves(service: SettingsService, entry: _Entry) -> None:
    """With no DB override the resolved value is the documented default."""
    namespace, key, _stype, default, _restart = entry
    value = await service.get(namespace, key)
    assert value.value == default
    assert value.source is SettingSource.DEFAULT
