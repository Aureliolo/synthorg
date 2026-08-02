# module-kind: adapter
"""Settings-to-provider wiring for the ask policy.

Reads the two ``engine.ask_policy_*`` keys into an :class:`AskPolicyConfig` and
binds the process-global ambient provider the prompt build reads. Shared by the
boot wiring hook and the settings subscriber, so a change reaches the next
prompt build without a restart.

Two targeted key reads rather than a namespace sweep: ``engine`` holds around a
hundred keys and this needs two of them.

**Fail to ON.** Every recoverable failure still binds an enabled provider. This
is the mirror image of the output-style posture and deliberately so: for
enforcement the conservative direction is to keep enforcing, and for asking it
is to keep asking. Leaving the provider unbound would silently stop the
organisation asking, which is the exact failure this subsystem exists to
prevent.
"""

import json
from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.ask_policy.models import AskDirective, AskPolicyConfig
from synthorg.engine.ask_policy.provider import (
    SnapshotAskPolicyProvider,
    set_ask_policy_provider,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.ask_policy import (
    ASK_POLICY_CONFIG_VALIDATED,
    ASK_POLICY_DIRECTIVES_INVALID,
    ASK_POLICY_PROVIDER_REBOUND,
)

if TYPE_CHECKING:
    from synthorg.settings.service import SettingsService

logger = get_logger(__name__)

_NAMESPACE = "engine"
_ENABLED_KEY = "ask_policy_enabled"
_EXTRA_DIRECTIVES_KEY = "ask_policy_extra_directives"


def _as_bool(value: str | None, *, default: bool) -> bool:
    """Parse a settings boolean string, falling back to *default* when unset.

    Returns:
        ``True`` when *value* is ``"true"`` (case-insensitive), the *default*
        when unset, else ``False``.
    """
    if value is None:
        return default
    return value.strip().casefold() == "true"


def _parse_extra_directives(raw: str | None) -> tuple[AskDirective, ...]:
    """Parse the operator-authored directives JSON setting.

    A malformed value yields no extras rather than raising: the standing
    directive is what matters, and one bad operator entry must not take the
    whole subsystem down. The write-time validator in
    ``settings/json_validators.py`` is what normally rejects a bad payload.

    Returns:
        The parsed directives, or an empty tuple on any parse error.
    """
    if not raw:
        return ()
    try:
        parsed = json.loads(raw)
    except ValueError, TypeError:
        logger.warning(
            ASK_POLICY_DIRECTIVES_INVALID, source="settings", note="not json"
        )
        return ()
    if not isinstance(parsed, list):
        logger.warning(
            ASK_POLICY_DIRECTIVES_INVALID,
            source="settings",
            note="not a list; ignored",
            actual_type=type(parsed).__name__,
        )
        return ()
    result: list[AskDirective] = []
    for entry in parsed:
        try:
            result.append(AskDirective.model_validate(entry))
        except (TypeError, ValueError) as exc:
            logger.warning(
                ASK_POLICY_DIRECTIVES_INVALID,
                source="settings",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ()
    return tuple(result)


async def build_ask_policy_config(
    settings_service: SettingsService,
) -> AskPolicyConfig:
    """Assemble the ``AskPolicyConfig`` from the two ``engine`` keys.

    Returns:
        The operator config.
    """
    enabled_value = await settings_service.get(_NAMESPACE, _ENABLED_KEY)
    extras_value = await settings_service.get(_NAMESPACE, _EXTRA_DIRECTIVES_KEY)
    config = AskPolicyConfig(
        enabled=_as_bool(enabled_value.value, default=True),
        extra_directives=_parse_extra_directives(extras_value.value),
    )
    logger.debug(
        ASK_POLICY_CONFIG_VALIDATED,
        enabled=config.enabled,
        extra_directive_count=len(config.extra_directives),
    )
    return config


def _bind(config: AskPolicyConfig) -> AskPolicyConfig:
    """Bind *config* as the ambient ask-policy provider.

    Returns:
        The bound config, so callers can ``return _bind(...)``.
    """
    set_ask_policy_provider(
        SnapshotAskPolicyProvider(config.extra_directives, enabled=config.enabled)
    )
    logger.info(
        ASK_POLICY_PROVIDER_REBOUND,
        enabled=config.enabled,
        extra_directive_count=len(config.extra_directives),
    )
    return config


async def rebuild_and_bind_ask_policy(
    settings_service: SettingsService,
) -> AskPolicyConfig:
    """Rebuild the ask policy from settings and bind the ambient provider.

    Returns:
        The freshly built and bound config. A recoverable settings failure
        binds the default (enabled, no extras) rather than leaving the
        organisation silently unable to ask.
    """
    try:
        config = await build_ask_policy_config(settings_service)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised; else fail-to-on
        # lint-allow: swallow-ok -- a recoverable settings-read failure must
        # still bind the standing directive. Unlike the output-style guardrail
        # this fails OPEN-as-in-asking, which is the conservative direction
        # here; criticals are re-raised by reraise_critical.
        reraise_critical(exc)
        logger.error(
            ASK_POLICY_DIRECTIVES_INVALID,
            source="config",
            action="bind_default_enabled",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return _bind(AskPolicyConfig())
    return _bind(config)


__all__ = ["build_ask_policy_config", "rebuild_and_bind_ask_policy"]
