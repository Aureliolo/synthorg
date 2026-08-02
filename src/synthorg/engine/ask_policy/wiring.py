# module-kind: adapter
"""Settings-to-provider wiring for the ask policy.

Reads the two ``engine.ask_policy_*`` keys into an :class:`AskPolicyConfig` and
binds the process-global ambient provider the prompt build reads. Shared by the
boot wiring hook and the settings subscriber, so a change reaches the next
prompt build without a restart.

Two targeted key reads rather than a namespace sweep: ``engine`` holds around a
hundred keys and this needs two of them.

**Fail to ON.** The organisation must never silently stop asking, so a
recoverable failure always leaves an enabled provider bound. Same rule as the
output-style guardrail, which collapses to a minimal still-enforcing pack: keep
the load-bearing behaviour running. It only reads as the opposite because for
output style that behaviour is "keep enforcing" and here it is "keep asking".

"Never unbound" is not the same as "always the shipped default", and the
difference matters most at the moment this is most likely to fail. The
subscriber re-reads immediately after an operator writes, so a transient
backend blip right then would otherwise discard the value they just set:
a deliberate, governance-audited ``disabled`` would silently revert to
enabled with nothing in the dashboard to show it. So a rebuild that cannot
read keeps whatever is already bound, and only an unbound provider (a cold
boot that never succeeded) falls back to the shipped default.
"""

import json
from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.ask_policy.models import AskDirective, AskPolicyConfig
from synthorg.engine.ask_policy.provider import (
    SnapshotAskPolicyProvider,
    current_ask_policy_provider,
    set_ask_policy_provider,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.ask_policy import (
    ASK_POLICY_CONFIG_VALIDATED,
    ASK_POLICY_DIRECTIVES_INVALID,
    ASK_POLICY_PROVIDER_REBOUND,
    ASK_POLICY_PROVIDER_RETAINED,
    ASK_POLICY_SETTINGS_READ_FAILED,
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

    One bad ENTRY costs only that entry. Discarding the whole list would take
    nine valid operator directives down with the tenth, silently, while
    ``GET /settings`` still showed all ten as configured.

    Returns:
        The parsed directives, minus any entry that failed validation. Empty
        when the payload as a whole is unparseable.
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
    for index, entry in enumerate(parsed):
        try:
            result.append(AskDirective.model_validate(entry))
        except (TypeError, ValueError) as exc:
            logger.warning(
                ASK_POLICY_DIRECTIVES_INVALID,
                source="settings",
                index=index,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
    return tuple(result)


async def _read_key(
    settings_service: SettingsService,
    key: str,
) -> tuple[str | None, bool]:
    """Read one ``engine`` key, reporting whether the read itself succeeded.

    Each key is resolved independently so a blip on the second cannot discard
    the first: a partial failure should cost only the key that failed.

    Returns:
        The raw value (``None`` when unset) and whether the read succeeded.
    """
    try:
        resolved = await settings_service.get(_NAMESPACE, key)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised; else per-key
        # lint-allow: swallow-ok -- a per-key read failure is reported to the
        # caller through the returned flag rather than raised, so one
        # unreadable key cannot discard a sibling that resolved fine.
        reraise_critical(exc)
        logger.error(
            ASK_POLICY_SETTINGS_READ_FAILED,
            key=key,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None, False
    return resolved.value, True


async def build_ask_policy_config(
    settings_service: SettingsService,
) -> tuple[AskPolicyConfig, bool]:
    """Assemble the ``AskPolicyConfig`` from the two ``engine`` keys.

    Returns:
        The operator config, and whether EVERY key read succeeded. A caller
        that already has a provider bound uses the flag to decide between
        rebinding and keeping what it has.
    """
    enabled_raw, enabled_ok = await _read_key(settings_service, _ENABLED_KEY)
    extras_raw, extras_ok = await _read_key(settings_service, _EXTRA_DIRECTIVES_KEY)
    config = AskPolicyConfig(
        enabled=_as_bool(enabled_raw, default=True),
        extra_directives=_parse_extra_directives(extras_raw),
    )
    logger.debug(
        ASK_POLICY_CONFIG_VALIDATED,
        enabled=config.enabled,
        extra_directive_count=len(config.extra_directives),
    )
    return config, enabled_ok and extras_ok


def bind_ask_policy_config(config: AskPolicyConfig) -> AskPolicyConfig:
    """Bind *config* as the ambient ask-policy provider.

    Exported so a host with no settings service (the boot hook before the
    settings backend exists, an eval harness) can still bind the shipped
    default rather than leaving the provider unbound, which reads as OFF.

    Returns:
        The bound config, so callers can ``return bind_ask_policy_config(...)``.
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
) -> AskPolicyConfig | None:
    """Rebuild the ask policy from settings and bind the ambient provider.

    Never raises for a settings fault: each key read reports failure through
    a flag instead (only a critical error propagates). Callers therefore do
    not wrap this in a try/except; an outer handler for "the settings read
    failed" would be dead code that reads as a second line of defence.

    Returns:
        The freshly bound config, or ``None`` when a read failed and the
        already-bound provider was kept instead. The organisation is asking
        either way; ``None`` says the binding is older than this call.
    """
    config, complete = await build_ask_policy_config(settings_service)
    if not complete and current_ask_policy_provider() is not None:
        # A read failed and something is already bound. Rebinding here would
        # replace the operator's persisted choice with the shipped default on
        # a transient blip, so the last known-good binding stands.
        logger.warning(ASK_POLICY_PROVIDER_RETAINED, reason="settings_read_failed")
        return None
    return bind_ask_policy_config(config)


__all__ = [
    "bind_ask_policy_config",
    "build_ask_policy_config",
    "rebuild_and_bind_ask_policy",
]
