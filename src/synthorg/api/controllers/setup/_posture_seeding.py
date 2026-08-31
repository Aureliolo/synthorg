# module-kind: code
"""Seed a template posture's settings-resident feature flags at setup.

A company template declares a named posture that resolves to a flag
bundle. The config-resident knobs (``security.red_team`` / ``budget``
numerics / ``knowledge_substrate`` grounding) are threaded into the
rendered ``RootConfig`` by the template renderer; this module writes the
settings-resident flags that the boot wiring and the live capability
gates read.

Every flag names the settings writes it stands for, so a flag the posture
leaves off stays at its registered default. A flag is declared here only
when its write actually differs from that default: steering, propose,
routing and group-chat all default on already, so a posture "enabling"
them has nothing left to write, and those flags carry no dispatch row.
``agent_invite`` and ``direct_mcp`` default off, so writing them is a real
opt-in; ``economical_reasoning`` is the one flag that lowers a value,
dropping the reasoning-effort ceiling at the two stakes levels that still
have room to give one up. The toolsmith is intentionally not posture
driven: enabling it needs an explicit, deployment-specific capability
allowlist, so it stays an operator opt-in.
"""

import asyncio

from synthorg.config.posture_config import PostureConfig
from synthorg.core.completion_enums import ReasoningEffort
from synthorg.observability import get_logger
from synthorg.observability.events.setup import SETUP_POSTURE_SEEDED
from synthorg.settings.service import SettingsService
from synthorg.templates.loader import load_template
from synthorg.templates.pack_loader import load_pack
from synthorg.templates.postures import resolve_template_posture
from synthorg.templates.schema import CompanyTemplate

logger = get_logger(__name__)

#: One notch down the reasoning ladder where there is a notch to give up:
#: low and normal already sit at the registered floor ("low"), so only high
#: and critical have room to move. That is what the cost-disciplined posture
#: buys, thinking budget rather than the capability floor, so the rung a
#: task must run on is unchanged.
_ECONOMICAL_REASONING: tuple[tuple[str, str, str], ...] = (
    ("engine", "reasoning_effort_high", ReasoningEffort.LOW.value),
    ("engine", "reasoning_effort_critical", ReasoningEffort.MEDIUM.value),
)

# Posture flag -> the settings writes it stands for. A flag names a write
# only when the write actually changes something: steering, propose,
# routing and group-chat all default on already, so a bundle that "turned
# them on" would write a value equal to the registered default, pinning
# that row against a future default change with no matching test failure
# to catch it. The agent-invite / direct-MCP knobs default off, so those
# writes are the meaningful opt-in.
_POSTURE_FLAG_SETTINGS: tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...] = (
    ("economical_reasoning", _ECONOMICAL_REASONING),
    ("agent_invite", (("chief_of_staff", "invite_enabled", "true"),)),
    # Both halves, because either alone is a feature that cannot materialise:
    # the actor's gate refuses without the bridge, and the bridge without the
    # actor grants a surface nothing conversational reaches. A posture that
    # wrote one left the subsystem permanently BLOCKED on a condition no
    # dashboard control could clear.
    (
        "direct_mcp",
        (
            ("chief_of_staff", "direct_mcp_enabled", "true"),
            ("security", "mcp_self_consumer_mode", "trust_scoped"),
        ),
    ),
)


async def seed_posture_settings(
    settings_svc: SettingsService,
    template: CompanyTemplate,
) -> str | None:
    """Seed the template posture's settings-resident feature flags.

    Resolves the template's effective posture (inheritance + pack union)
    and writes the settings the boot wiring and live gates read. No-op
    when the template declares no posture.

    Args:
        settings_svc: The settings service to write into.
        template: The selected company template.

    Returns:
        The seeded posture name, or ``None`` when no posture applied.
    """
    # ``resolve_template_posture`` walks the template inheritance chain and
    # reads pack/parent YAML files from disk; offload the synchronous file
    # I/O so it does not block the event loop during setup.
    posture = await asyncio.to_thread(
        resolve_template_posture,
        template,
        load_pack=lambda name: load_pack(name).template,
        load_parent=lambda name: load_template(name).template,
    )
    if posture is None:
        return None
    await _write_posture_flags(settings_svc, posture)
    logger.info(SETUP_POSTURE_SEEDED, posture=posture.name)
    return posture.name


async def _write_posture_flags(
    settings_svc: SettingsService,
    posture: PostureConfig,
) -> None:
    """Write the settings each flag the posture requests stands for.

    All requested writes go out in a single ``set_many`` transaction so a
    concurrent live gate never observes a half-applied posture (some flags
    on, the rest still at their default).
    """
    items = [
        write
        for flag, writes in _POSTURE_FLAG_SETTINGS
        if getattr(posture, flag)
        for write in writes
    ]
    if not items:
        return
    await settings_svc.set_many(items, expected_updated_at_map={})
