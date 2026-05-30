"""Boot-time setting resolvers + the default approval-timeout scheduler.

Pure construction helpers relocated out of ``create_app``: Cat-2 boot-knob
resolvers that read ``env > registered default`` before the SettingsService
connects, plus the safe-default approval-timeout scheduler builder.
"""

from typing import Final

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.security.timeout.policies import WaitForeverPolicy
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
) -> ApprovalTimeoutScheduler:
    """Construct an :class:`ApprovalTimeoutScheduler` with safe defaults.

    Uses :class:`WaitForeverPolicy` so the scheduler runs the periodic
    scan and emits TIMEOUT_WAITING events but never auto-decides
    pending approvals. Operators wire a real policy via the
    ``security.timeout_*`` settings; the settings subscriber on
    ``security.timeout_check_interval_seconds`` invokes
    ``scheduler.reschedule()`` so the cadence stays operator-tunable
    without restart.

    Returns:
        The configured approval-timeout scheduler.
    """
    timeout_checker = TimeoutChecker(policy=WaitForeverPolicy())
    return ApprovalTimeoutScheduler(
        approval_store=approval_store,
        timeout_checker=timeout_checker,
        interval_seconds=_DEFAULT_TIMEOUT_CHECK_INTERVAL_SECONDS,
    )
