"""Boot-time setting resolvers + the default approval-timeout scheduler.

Pure construction helpers: Cat-2 boot-knob resolvers that read
``env > registered default`` before the SettingsService connects, plus the
safe-default approval-timeout scheduler builder.
"""

from typing import Final

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.observability import get_logger
from synthorg.observability.events.timeout import TIMEOUT_FACTORY_UNKNOWN_CONFIG
from synthorg.security.timeout.config import ApprovalTimeoutConfig
from synthorg.security.timeout.factory import create_timeout_policy
from synthorg.security.timeout.policies import WaitForeverPolicy
from synthorg.security.timeout.protocol import TimeoutPolicy
from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler
from synthorg.security.timeout.timeout_checker import TimeoutChecker
from synthorg.settings.bootstrap_resolver import resolve_init_value
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import (
    parse_bool,
    parse_str_tuple_json,
    resolve_init_int,
)

# Default approval-timeout interval mirrors the registry default for
# ``security.timeout_check_interval_seconds`` defined in
# ``src/synthorg/settings/definitions/security.py``. Held here as a
# constant so the bootstrap and the registry definition cannot drift;
# future reads from ConfigResolver still override at runtime via the
# scheduler's ``reschedule()`` (called from a settings subscriber).
# Update both sites together if the default ever changes; otherwise a
# bootstrap value will silently disagree with operator-editable
# overrides resolved through ``ConfigResolver``.
_DEFAULT_TIMEOUT_CHECK_INTERVAL_SECONDS: Final[float] = 60.0

logger = get_logger(__name__)


def _resolve_timeout_policy(config: ApprovalTimeoutConfig | None) -> TimeoutPolicy:
    """Build the timeout policy from the resolved approval-timeout config.

    Falls back to :class:`WaitForeverPolicy` (the safe, never-auto-decide
    default) when no config is supplied or the config maps to an
    unrecognised policy type, so a malformed company template degrades to
    the conservative behaviour rather than failing boot.

    Returns:
        The configured timeout policy, or the wait-forever default.
    """
    if config is None:
        return WaitForeverPolicy()
    try:
        return create_timeout_policy(config)
    except TypeError:
        logger.warning(
            TIMEOUT_FACTORY_UNKNOWN_CONFIG,
            config_type=type(config).__name__,
        )
        return WaitForeverPolicy()


def resolve_rate_limiter_enabled() -> bool:
    """Resolve ``api.rate_limiter_enabled`` at app construction time.

    Cat-2 (``read_only_post_init=True``): env > default. The
    ``SettingsService`` rejects runtime mutation, so the value baked
    here lives for the process lifetime.

    Returns:
        Whether the global rate limiter is enabled.
    """
    resolved = resolve_init_value(
        SettingNamespace.API,
        "rate_limiter_enabled",
        parse=parse_bool,
    )
    return bool(resolved.value)


def resolve_api_str_tuple(key: str) -> tuple[str, ...]:
    """Resolve a JSON-tuple-typed api.* setting at boot.

    When the parsed value is not a tuple (e.g. invalid JSON returns None
    from the parser), the resolver applies the registered default, which
    is always a valid tuple, so this function always returns a tuple.

    Returns:
        The resolved string tuple.
    """
    resolved = resolve_init_value(
        SettingNamespace.API,
        key,
        parse=parse_str_tuple_json,
    )
    if isinstance(resolved.value, tuple):
        return resolved.value
    return ()


def resolve_api_int(key: str) -> int:
    """Resolve an integer-typed api.* setting at boot.

    Non-integer env values fall through to the registered default rather
    than raising at app construction time.

    Returns:
        The resolved integer value.
    """
    return resolve_init_int(SettingNamespace.API, key)


def resolve_api_str(key: str) -> str:
    """Resolve a string-typed api.* setting at boot.

    Returns:
        The resolved string value.
    """
    resolved = resolve_init_value(SettingNamespace.API, key)
    return str(resolved.value)


def resolve_budget_int(key: str) -> int:
    """Resolve an integer-typed budget.* setting at boot.

    Cat-2 boot knob: the store is constructed before the
    ``SettingsService`` connects, so the value is sourced env >
    registered default via the bootstrap resolver (a runtime change
    requires a restart -- the consumer is a fixed-length ring buffer).

    Returns:
        The resolved integer value.
    """
    return resolve_init_int(SettingNamespace.BUDGET, key)


def build_default_approval_timeout_scheduler(
    *,
    approval_store: ApprovalStoreProtocol,
    approval_timeout_config: ApprovalTimeoutConfig | None = None,
) -> ApprovalTimeoutScheduler:
    """Construct an :class:`ApprovalTimeoutScheduler` from the boot config.

    The timeout policy is resolved from ``approval_timeout_config`` (the
    resolved ``config.approval_timeout`` company-template field) via
    :func:`create_timeout_policy`, so an operator who configures
    deny-on-timeout / tiered / escalation-chain behaviour gets it from
    the first scan rather than the never-auto-decide default. When no
    config is supplied (or it maps to an unrecognised type), it degrades
    to :class:`WaitForeverPolicy`. The cadence stays operator-tunable
    without restart: the settings subscriber on
    ``security.timeout_check_interval_seconds`` invokes
    ``scheduler.reschedule()``.

    Returns:
        The configured approval-timeout scheduler.
    """
    timeout_checker = TimeoutChecker(
        policy=_resolve_timeout_policy(approval_timeout_config),
    )
    return ApprovalTimeoutScheduler(
        approval_store=approval_store,
        timeout_checker=timeout_checker,
        interval_seconds=_DEFAULT_TIMEOUT_CHECK_INTERVAL_SECONDS,
    )
