"""Live gate for autonomous Chief-of-Staff capabilities.

The autonomous Chief-of-Staff capabilities (alerts, narrative, learning,
invite, routing) are each governed by two switches: the persona master
switch ``self_improvement.chief_of_staff_enabled`` and the per-capability
flag ``chief_of_staff.<key>``.  A capability is live only when BOTH are
enabled, so turning the persona off suspends every autonomous behaviour at
once without touching the individual flags.

The user-initiated conversational capabilities (explain-chat, propose,
group-chat, direct-MCP acting) are deliberately NOT routed through here:
explain-chat, propose, and group-chat default on and are gated by their
own flag alone, and direct-MCP acting is fail-closed with its own
boot-time governance gate plus a live per-request feature gate. In every
case the off-by-default persona switch cannot silently disable them.
"""

from synthorg.settings.enums import SettingNamespace
from synthorg.settings.kill_switch import resolve_bool_with_fallback
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

_MASTER_KEY = "chief_of_staff_enabled"


async def resolve_cos_autonomous_cap(
    *,
    resolver: ConfigResolverProtocol | None,
    key: str,
    master_fallback: bool,
    cap_fallback: bool,
) -> bool:
    """Resolve whether an autonomous Chief-of-Staff capability is live.

    Reads the persona master switch first and short-circuits when it is
    off, so a disabled persona never spends on a capability lookup.  Both
    reads fall back through :func:`resolve_bool_with_fallback`, so a missing
    resolver or a settings outage degrades to the baked-config values rather
    than flip-flopping the capability.

    Args:
        resolver: The application's config resolver, or ``None`` when the
            caller is not yet wired.
        key: The per-capability ``chief_of_staff`` flag key (e.g.
            ``"alerts_enabled"``).
        master_fallback: Baked ``chief_of_staff_enabled`` value, used when
            the resolver is absent or the master lookup fails.
        cap_fallback: Baked per-capability value, used when the resolver is
            absent or the capability lookup fails.

    Returns:
        ``True`` only when both the master switch and the capability flag
        resolve to ``True``.
    """
    master = await resolve_bool_with_fallback(
        resolver=resolver,
        namespace=SettingNamespace.SELF_IMPROVEMENT,
        key=_MASTER_KEY,
        fallback=master_fallback,
    )
    if not master:
        return False
    return await resolve_bool_with_fallback(
        resolver=resolver,
        namespace=SettingNamespace.CHIEF_OF_STAFF,
        key=key,
        fallback=cap_fallback,
    )


__all__ = ["resolve_cos_autonomous_cap"]
