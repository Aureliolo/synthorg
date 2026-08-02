"""Conformance for the feature-enablement flags + per-feature models.

Each Chief-of-Staff / self-improvement / knowledge feature-enablement flag (and
each per-feature model) is asserted on three axes:

1. Registry presence with the documented default + type.
2. Default resolution through ``SettingsService.get()`` (DB empty).
3. That the flag is live, not compose-set: a capability an operator turns on
   is worthless if turning it on needs a redeploy.

The on-by-default posture lives in these defaults: only the four
off-categories (self-modification, autonomous spend, egress,
acts-on-your-behalf) ship ``"false"``; the conversational + knowledge
capabilities ship ``"true"``.
"""

import pytest

from synthorg.persistence.settings_protocol import SettingsRepository
from synthorg.settings import definitions as _definitions  # noqa: F401
from synthorg.settings.enums import SettingSource, SettingType
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests._shared import mock_of

pytestmark = pytest.mark.unit


@pytest.fixture
def service() -> SettingsService:
    """A settings service over an empty repository (defaults resolve)."""
    repo = mock_of[SettingsRepository]()
    repo.get.return_value = None
    repo.get_namespace.return_value = ()
    repo.list_items.return_value = ()
    return SettingsService(repository=repo, registry=get_registry())


# (namespace, key, type, default).
_ENTRIES: tuple[tuple[str, str, SettingType, str], ...] = (
    # Conversational Chief-of-Staff capabilities: on, live-gated at the HTTP
    # controller (built at boot on-by-default).
    ("chief_of_staff", "explain_chat_enabled", SettingType.BOOLEAN, "true"),
    ("chief_of_staff", "propose_enabled", SettingType.BOOLEAN, "true"),
    ("chief_of_staff", "group_chat_enabled", SettingType.BOOLEAN, "true"),
    # Routing is gated per turn in the proposer.
    ("chief_of_staff", "routing_enabled", SettingType.BOOLEAN, "true"),
    # Autonomous capabilities: off, gated per cycle / per turn, or
    # started/stopped by a settings subscriber.
    ("chief_of_staff", "learning_enabled", SettingType.BOOLEAN, "false"),
    ("chief_of_staff", "alerts_enabled", SettingType.BOOLEAN, "false"),
    ("chief_of_staff", "narrative_enabled", SettingType.BOOLEAN, "false"),
    # Agent invite: off, gated per group-chat turn.
    ("chief_of_staff", "invite_enabled", SettingType.BOOLEAN, "false"),
    # Direct MCP acting: off, fail-closed (needs security governance + the MCP
    # self-consumer wired). A toggle rebuilds the actor through that same gate.
    ("chief_of_staff", "direct_mcp_enabled", SettingType.BOOLEAN, "false"),
    # Per-feature models: blank by default (setup auto-selects); read live
    # per LLM call.
    ("chief_of_staff", "chat_model", SettingType.MODEL_REF, ""),
    ("chief_of_staff", "propose_model", SettingType.MODEL_REF, ""),
    ("chief_of_staff", "routing_model", SettingType.MODEL_REF, ""),
    ("chief_of_staff", "narrative_model", SettingType.MODEL_REF, ""),
    # Self-modification: every switch off (config_tuning only matters when
    # the master is on). The meta-loop re-reads them live.
    ("self_improvement", "enabled", SettingType.BOOLEAN, "false"),
    ("self_improvement", "chief_of_staff_enabled", SettingType.BOOLEAN, "false"),
    ("self_improvement", "config_tuning_enabled", SettingType.BOOLEAN, "true"),
    (
        "self_improvement",
        "architecture_proposals_enabled",
        SettingType.BOOLEAN,
        "false",
    ),
    ("self_improvement", "prompt_tuning_enabled", SettingType.BOOLEAN, "false"),
    ("self_improvement", "code_modification_enabled", SettingType.BOOLEAN, "false"),
    ("self_improvement", "tool_creation_enabled", SettingType.BOOLEAN, "false"),
    ("self_improvement", "analysis_model", SettingType.MODEL_REF, ""),
    ("self_improvement", "code_modification_model", SettingType.MODEL_REF, ""),
    # Knowledge: on by default, no model of its own; ghost-wired and
    # live-gated at the knowledge tools.
    ("knowledge", "enabled", SettingType.BOOLEAN, "true"),
)


type _Entry = tuple[str, str, SettingType, str]

# Readable parametrize ids ("chief_of_staff/propose_enabled") so a failing
# case names the namespace/key instead of "entry7".
_ENTRY_IDS = [f"{ns}/{key}" for ns, key, *_ in _ENTRIES]


@pytest.mark.parametrize("entry", _ENTRIES, ids=_ENTRY_IDS)
def test_entry_registered_with_default(entry: _Entry) -> None:
    """The definition exists with the documented type and default, and is live."""
    namespace, key, stype, default = entry
    defn = get_registry().get(namespace, key)
    assert defn is not None, f"{namespace}/{key} not registered"
    assert defn.type is stype
    assert defn.default == default
    assert defn.compose_set is False


@pytest.mark.parametrize("entry", _ENTRIES, ids=_ENTRY_IDS)
async def test_default_resolves(service: SettingsService, entry: _Entry) -> None:
    """With no DB override the resolved value is the documented default."""
    namespace, key, _stype, default = entry
    value = await service.get(namespace, key)
    assert value.value == default
    assert value.source is SettingSource.DEFAULT
