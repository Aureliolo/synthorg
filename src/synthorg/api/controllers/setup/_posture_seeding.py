# module-kind: code
"""Seed a template posture's settings-resident feature flags at setup.

A company template declares a named posture that resolves to a flag bundle.
The config-resident knobs (``security.red_team`` / ``budget`` numerics) are
threaded into the rendered ``RootConfig`` by the template renderer; this
module writes the *settings-resident* flags that the best-effort boot wiring
reads: the conversational chat modes on ``meta.self_improvement``, the steering
proposer on ``cockpit.steering_proposer_enabled``, and the budget auto-downgrade
on ``budget.auto_downgrade_enabled``.

The toolsmith is intentionally not posture driven: enabling it needs an
explicit, deployment-specific capability allowlist, so it stays an operator
opt-in.
"""

from synthorg.observability import get_logger
from synthorg.observability.events.setup import SETUP_POSTURE_SEEDED
from synthorg.settings.service import SettingsService
from synthorg.templates.loader import load_template
from synthorg.templates.pack_loader import load_pack
from synthorg.templates.postures import resolve_template_posture
from synthorg.templates.schema import CompanyTemplate

logger = get_logger(__name__)


def _bool_setting(value: bool) -> str:  # noqa: FBT001
    """Render a boolean as the settings-service string form.

    Returns:
        ``"true"`` or ``"false"``.
    """
    return "true" if value else "false"


async def seed_posture_settings(
    settings_svc: SettingsService,
    template: CompanyTemplate,
) -> str | None:
    """Seed the template posture's settings-resident feature flags.

    Resolves the template's effective posture (inheritance + pack union) and
    writes the settings the boot wiring reads. No-op when the template
    declares no posture.

    Args:
        settings_svc: The settings service to write into.
        template: The selected company template.

    Returns:
        The seeded posture name, or ``None`` when no posture applied.
    """
    from synthorg.meta.config import (  # noqa: PLC0415
        SelfImprovementConfig,
        load_self_improvement_config,
    )

    posture = resolve_template_posture(
        template,
        load_pack=lambda name: load_pack(name).template,
        load_parent=lambda name: load_template(name).template,
    )
    if posture is None:
        return None

    base = await load_self_improvement_config(settings_svc)
    updated = base.model_copy(
        update={
            "chief_of_staff": base.chief_of_staff.model_copy(
                update={
                    "propose_enabled": posture.chat_propose,
                    "routing_enabled": posture.chat_routing,
                    "group_chat_enabled": posture.group_chat,
                    "invite_enabled": posture.agent_invite,
                    "direct_mcp_enabled": posture.direct_mcp,
                },
            ),
        },
    )
    # Re-validate so the persisted blob is guaranteed coherent (model_copy
    # bypasses validators; load_self_improvement_config re-validates at boot).
    coherent = SelfImprovementConfig.model_validate(updated.model_dump())
    await settings_svc.set("meta", "self_improvement", coherent.model_dump_json())
    await settings_svc.set(
        "cockpit",
        "steering_proposer_enabled",
        _bool_setting(posture.steering),
    )
    await settings_svc.set(
        "budget",
        "auto_downgrade_enabled",
        _bool_setting(posture.auto_downgrade),
    )
    logger.info(SETUP_POSTURE_SEEDED, posture=posture.name)
    return posture.name
