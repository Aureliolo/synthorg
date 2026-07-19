# module-kind: adapter
"""Settings-to-service wiring for the output-style policy.

Reads the ``output_style`` settings namespace into an :class:`OutputStyleConfig`,
builds the :class:`OutputStylePolicyService`, and binds the process-global
ambient service (used by every output boundary) plus the soft-layer house-style
provider (used by the prompt build). Shared by the boot wiring hook and the
settings subscriber so a config change re-binds both on the next boundary check
and prompt build without a restart.
"""

import asyncio
import json
from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.output_style.models import OutputStyleConfig, SanctionedExemption
from synthorg.engine.output_style.pack_loader import minimal_failclosed_pack
from synthorg.engine.output_style.provider import set_house_style_provider
from synthorg.engine.output_style.service import (
    OutputStylePolicyService,
    set_output_policy_service,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.output_style import (
    OUTPUT_STYLE_CONFIG_VALIDATED,
    OUTPUT_STYLE_PACK_INVALID,
    OUTPUT_STYLE_SERVICE_REBUILT,
    OUTPUT_STYLE_SNAPSHOT_REFRESHED,
)

if TYPE_CHECKING:
    from synthorg.settings.service import SettingsService

logger = get_logger(__name__)


def _as_bool(value: str | None, *, default: bool) -> bool:
    """Parse a settings boolean string, falling back to *default* when unset.

    Returns:
        ``True`` when *value* is ``"true"`` (case-insensitive), the *default*
        when unset, else ``False``.
    """
    if value is None:
        return default
    return value.strip().casefold() == "true"


def _parse_exemptions(raw: str | None) -> tuple[SanctionedExemption, ...]:
    """Parse the ``exemptions`` JSON setting into sanctioned exemptions.

    A malformed value yields no exemptions (fail-safe: an unparseable value
    must never silently widen the guardrail). Each valid entry is validated.

    Returns:
        The parsed exemptions, or an empty tuple on any parse/validation error.
    """
    if not raw:
        return ()
    try:
        parsed = json.loads(raw)
    except ValueError, TypeError:
        logger.warning(OUTPUT_STYLE_PACK_INVALID, source="exemptions", note="not json")
        return ()
    if not isinstance(parsed, list):
        logger.warning(
            OUTPUT_STYLE_PACK_INVALID,
            source="exemptions",
            note="not a list; ignored",
            actual_type=type(parsed).__name__,
        )
        return ()
    result: list[SanctionedExemption] = []
    for entry in parsed:
        try:
            result.append(SanctionedExemption.model_validate(entry))
        except (TypeError, ValueError) as exc:
            logger.warning(
                OUTPUT_STYLE_PACK_INVALID,
                source="exemptions",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
    return tuple(result)


async def build_output_style_config(
    settings_service: SettingsService,
) -> OutputStyleConfig:
    """Assemble the ``OutputStyleConfig`` from the output_style namespace.

    Returns:
        The operator config (one batched namespace read).
    """
    values = {
        entry.definition.key: entry.value
        for entry in await settings_service.get_namespace("output_style")
    }
    pack = (values.get("pack") or "default").strip() or "default"
    config = OutputStyleConfig(
        enabled=_as_bool(values.get("enabled"), default=True),
        shadow_mode=_as_bool(values.get("shadow_mode"), default=False),
        pack=pack,
        house_style_enabled=_as_bool(values.get("house_style_enabled"), default=True),
        exemptions=_parse_exemptions(values.get("exemptions")),
    )
    logger.debug(
        OUTPUT_STYLE_CONFIG_VALIDATED,
        pack=config.pack,
        enabled=config.enabled,
        shadow_mode=config.shadow_mode,
        exemption_count=len(config.exemptions),
    )
    return config


def _bind(service: OutputStylePolicyService) -> OutputStylePolicyService:
    """Bind *service* as the ambient output-policy + house-style provider.

    Returns:
        The bound service, so callers can ``return _bind(...)``.
    """
    set_output_policy_service(service)
    set_house_style_provider(service.build_house_style_provider())
    logger.info(
        OUTPUT_STYLE_SERVICE_REBUILT, pack=service.pack.name, enabled=service.enabled
    )
    logger.debug(OUTPUT_STYLE_SNAPSHOT_REFRESHED, pack=service.pack.name)
    return service


async def rebuild_and_bind_output_style(
    settings_service: SettingsService,
) -> OutputStylePolicyService:
    """Rebuild the policy service from settings and bind the ambient hooks.

    Loads the configured pack, compiles the evaluator, and binds the ambient
    output-policy service (for the boundaries) and the house-style provider
    (for the prompt build). Pack loading is blocking file I/O, so it runs off
    the event loop. Enforcement must never silently disable, so every
    recoverable failure still binds a service rather than leaving the
    guardrail unbound: a malformed settings read binds the in-code em-dash ban
    on safe defaults; a bad pack name / invalid pack falls back to the built-in
    default; a corrupted default resource or any unexpected error binds the
    in-code fail-closed pack. Only a critical error propagates.

    Returns:
        The freshly built and bound service (always bound, fail-closed on any
        recoverable error).
    """
    from synthorg.engine.output_style.errors import (  # noqa: PLC0415
        OutputStyleError,
        OutputStylePackNotFoundError,
    )

    try:
        config = await build_output_style_config(settings_service)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised; else fail-closed
        reraise_critical(exc)
        logger.error(
            OUTPUT_STYLE_PACK_INVALID,
            source="config",
            action="fail_closed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return _bind(
            OutputStylePolicyService(
                pack=minimal_failclosed_pack(), config=OutputStyleConfig()
            )
        )

    try:
        service = await asyncio.to_thread(OutputStylePolicyService.from_config, config)
    except (OutputStyleError, OutputStylePackNotFoundError) as exc:
        logger.warning(
            OUTPUT_STYLE_PACK_INVALID,
            pack_name=config.pack,
            action="fallback_to_default",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        try:
            service = await asyncio.to_thread(
                OutputStylePolicyService.from_config,
                config.model_copy(update={"pack": "default"}),
            )
        except (OutputStyleError, OutputStylePackNotFoundError) as exc2:
            logger.error(
                OUTPUT_STYLE_PACK_INVALID,
                pack_name="default",
                action="fail_closed",
                error_type=type(exc2).__name__,
                error=safe_error_description(exc2),
            )
            service = OutputStylePolicyService(
                pack=minimal_failclosed_pack(), config=config
            )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised; else fail-closed
        reraise_critical(exc)
        logger.error(
            OUTPUT_STYLE_PACK_INVALID,
            pack_name=config.pack,
            action="fail_closed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        service = OutputStylePolicyService(
            pack=minimal_failclosed_pack(), config=config
        )
    return _bind(service)


__all__ = [
    "build_output_style_config",
    "rebuild_and_bind_output_style",
]
