# module-kind: code
"""Seed a template posture's settings-resident feature flags at setup.

A company template declares a named posture that resolves to a flag
bundle. The config-resident knobs (``security.red_team`` / ``budget``
numerics / ``knowledge_substrate`` grounding) are threaded into the
rendered ``RootConfig`` by the template renderer; this module writes the
settings-resident flags that the boot wiring and the live capability
gates read.

Postures are additive: a bundle only ever turns a flag on, so seeding is
upgrade-only. It writes ``"true"`` for each capability the posture
requests and never downgrades the on-by-default global posture (a flag
the posture leaves off stays at its registered default). The toolsmith is
intentionally not posture driven: enabling it needs an explicit,
deployment-specific capability allowlist, so it stays an operator opt-in.
"""

import asyncio

from synthorg.config.posture_config import PostureConfig
from synthorg.observability import get_logger
from synthorg.observability.events.setup import SETUP_POSTURE_SEEDED
from synthorg.settings.service import SettingsService
from synthorg.templates.loader import load_template
from synthorg.templates.pack_loader import load_pack
from synthorg.templates.postures import resolve_template_posture
from synthorg.templates.schema import CompanyTemplate

logger = get_logger(__name__)

# Posture flag -> (settings namespace, key): the real knob the posture's
# boolean turns on. Conversational + steering capabilities are on by
# default, so a write here is a redundant-but-faithful record of the
# template's intent; the agent-invite / direct-MCP knobs default off, so
# the write is the meaningful opt-in. Postures never write "false".
_POSTURE_FLAG_SETTINGS: tuple[tuple[str, str, str], ...] = (
    ("steering", "cockpit", "steering_proposer_enabled"),
    ("auto_downgrade", "budget", "auto_downgrade_enabled"),
    ("chat_propose", "chief_of_staff", "propose_enabled"),
    ("chat_routing", "chief_of_staff", "routing_enabled"),
    ("group_chat", "chief_of_staff", "group_chat_enabled"),
    ("agent_invite", "chief_of_staff", "invite_enabled"),
    ("direct_mcp", "chief_of_staff", "direct_mcp_enabled"),
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
    """Write ``"true"`` for each capability the posture requests."""
    for flag, namespace, key in _POSTURE_FLAG_SETTINGS:
        if getattr(posture, flag):
            await settings_svc.set(namespace, key, "true")
