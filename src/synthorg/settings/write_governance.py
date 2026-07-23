"""Deliberate-action guardrail for security-weakening settings writes.

Turning a security toggle off (switching the output-scan policy to the
permissive ``log_only``, disabling the MCP sandbox, giving a sandbox container
the host network, or lifting its CPU quota) reduces the running security
posture. Because those settings are now hot-reloadable, the write path enforces
a deliberate confirm + reason + actor for the weakening direction so neither an
HTTP import, an MCP handler, nor a CLI/import path can silently disable scanning,
audit, or an isolation boundary. Enabling / tightening is unguarded and applies
immediately.

The guard is enforced centrally in :class:`SettingsService` (both the single
and batch write paths) so every surface inherits it; callers thread a
:class:`SettingsWriteGovernance` through ``set`` / ``set_many``.
"""

import json
from collections.abc import Awaitable, Callable, Iterable, Sequence
from typing import Final

from pydantic import BaseModel, ConfigDict

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.normalization import compare_ci, normalize_identifier
from synthorg.core.task_enums import Stakes, compare_stakes
from synthorg.observability import get_logger
from synthorg.observability.events.settings import (
    SETTINGS_SECURITY_GOVERNANCE_CONFIRMED,
    SETTINGS_VALIDATION_FAILED,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.errors import SecurityToggleConfirmationRequiredError
from synthorg.settings.models import SettingDefinition, SettingValue

logger = get_logger(__name__)

_SECURITY_NS: Final[str] = SettingNamespace.SECURITY.value
_ENGINE_NS: Final[str] = SettingNamespace.ENGINE.value
_TOOLS_NS: Final[str] = SettingNamespace.TOOLS.value
_OUTPUT_STYLE_NS: Final[str] = SettingNamespace.OUTPUT_STYLE.value
_PROVIDERS_NS: Final[str] = SettingNamespace.PROVIDERS.value

# Enabling the LLM gateway opens an OpenAI-compatible egress path that lets an
# embedded harness make provider calls, so the ``false -> true`` transition is
# the weakening direction and routes through the deliberate guardrail; disabling
# it (closing the egress) tightens and is unguarded.
_GATEWAY_ENABLED_KEY: Final[str] = "gateway_enabled"

# Output-style keys whose change relaxes the running guardrail: disabling the
# whole policy, switching every rule to shadow (surface but never block), adding
# a sanctioned exemption (which lets an agent legitimately emit an
# otherwise-banned literal in a matching scope), or swapping the active rule pack
# (a different pack can drop or soften every hard rule, so a pack swap can gut
# the guardrail as fully as disabling it). Each routes through the same
# deliberate confirm+reason+actor guardrail.
_OUTPUT_STYLE_ENABLED_KEY: Final[str] = "enabled"
_OUTPUT_STYLE_SHADOW_KEY: Final[str] = "shadow_mode"
_OUTPUT_STYLE_EXEMPTIONS_KEY: Final[str] = "exemptions"
_OUTPUT_STYLE_PACK_KEY: Final[str] = "pack"
_OUTPUT_STYLE_GUARDED_KEYS: Final[frozenset[str]] = frozenset(
    {
        _OUTPUT_STYLE_ENABLED_KEY,
        _OUTPUT_STYLE_SHADOW_KEY,
        _OUTPUT_STYLE_EXEMPTIONS_KEY,
        _OUTPUT_STYLE_PACK_KEY,
    }
)
_OUTPUT_STYLE_ENABLED_DEFAULT: Final[str] = "true"
_OUTPUT_STYLE_PACK_DEFAULT: Final[str] = "default"

# Boolean security toggles whose ``true -> false`` transition weakens posture.
_WEAKENING_BOOL_KEYS: Final[frozenset[str]] = frozenset(
    {"enabled", "audit_enabled", "post_tool_scanning_enabled"}
)
# The permissive output-scan policy: switching TO it weakens posture.
_OUTPUT_SCAN_POLICY_KEY: Final[str] = "output_scan_policy_type"
_PERMISSIVE_OUTPUT_SCAN_POLICY: Final[str] = "log_only"

# Completion-oracle keys in the ``engine`` namespace that relax independent
# verification. Disabling the oracle, switching it to shadow mode (every REJECT
# becomes a logged no-op), or raising the stakes floor so fewer tasks are
# reviewed each drop the running verification posture, so they route through
# the same deliberate confirm+reason+actor guardrail as the security toggles.
_ENGINE_ORACLE_DISABLE_KEY: Final[str] = "completion_oracle_enabled"
_ENGINE_ORACLE_SHADOW_KEY: Final[str] = "completion_oracle_shadow_mode"
_ENGINE_ORACLE_MIN_STAKES_KEY: Final[str] = "completion_oracle_min_stakes"
_ENGINE_GUARDED_KEYS: Final[frozenset[str]] = frozenset(
    {
        _ENGINE_ORACLE_DISABLE_KEY,
        _ENGINE_ORACLE_SHADOW_KEY,
        _ENGINE_ORACLE_MIN_STAKES_KEY,
    }
)
# Registered default for the enable toggle, consulted when the key is unset so
# a first explicit weakening write (no stored current) is still guarded.
_ENGINE_ORACLE_ENABLED_DEFAULT: Final[str] = "true"

# MCP sandbox isolation keys in the ``tools`` namespace. Disabling the sandbox,
# switching a container to the host network namespace, or lifting the CPU cgroup
# cap each remove an isolation boundary around an untrusted stdio MCP server, so
# they route through the same deliberate confirm+reason+actor guardrail.
_MCP_SANDBOX_ENABLED_KEY: Final[str] = "mcp_sandbox_enabled"
_MCP_SANDBOX_NETWORK_KEY: Final[str] = "mcp_sandbox_network"
_MCP_SANDBOX_CPUS_KEY: Final[str] = "mcp_sandbox_cpus"
_CREDENTIALED_MCP_ENABLED_KEY: Final[str] = "credentialed_mcp_enabled"
_CREDENTIALED_MCP_CAPABILITIES_KEY: Final[str] = "credentialed_mcp_capabilities"
# Deploy reaches an external system that runs a live product, so enabling the
# capability or adding a target widens real blast radius, not just permission.
_DEPLOY_TOOLS_ENABLED_KEY: Final[str] = "deploy_tools_enabled"
_DEPLOY_TOOLS_TARGETS_KEY: Final[str] = "deploy_tools_targets"
# Publish reaches an external registry that serves running images, so enabling
# the capability or adding a target widens real blast radius, not just
# permission.
_PUBLISH_TOOLS_ENABLED_KEY: Final[str] = "publish_tools_enabled"
_PUBLISH_TOOLS_TARGETS_KEY: Final[str] = "publish_tools_targets"
# Each destructive, externally-reaching tool family guards its enable + target
# keys identically, so they share the weakening check rather than repeating a
# per-family branch that would grow with every new family.
_TOOL_FAMILY_ENABLED_KEYS: Final[frozenset[str]] = frozenset(
    {_DEPLOY_TOOLS_ENABLED_KEY, _PUBLISH_TOOLS_ENABLED_KEY}
)
_TOOL_FAMILY_TARGETS_KEYS: Final[frozenset[str]] = frozenset(
    {_DEPLOY_TOOLS_TARGETS_KEY, _PUBLISH_TOOLS_TARGETS_KEY}
)
_MCP_SANDBOX_GUARDED_KEYS: Final[frozenset[str]] = frozenset(
    {
        _MCP_SANDBOX_ENABLED_KEY,
        _MCP_SANDBOX_NETWORK_KEY,
        _MCP_SANDBOX_CPUS_KEY,
        _CREDENTIALED_MCP_ENABLED_KEY,
        _CREDENTIALED_MCP_CAPABILITIES_KEY,
        _DEPLOY_TOOLS_ENABLED_KEY,
        _DEPLOY_TOOLS_TARGETS_KEY,
        _PUBLISH_TOOLS_ENABLED_KEY,
        _PUBLISH_TOOLS_TARGETS_KEY,
    }
)
_MCP_SANDBOX_ENABLED_DEFAULT: Final[str] = "true"
_MCP_SANDBOX_NETWORK_DEFAULT: Final[str] = "bridge"
# Network isolation strength, most-isolated first: ``none`` blocks all egress,
# ``bridge`` allows egress through a NAT'd interface, ``host`` shares the host
# network namespace. A move toward a lower rank (e.g. none -> bridge, or
# bridge -> host) relaxes isolation and is guarded; the reverse strengthens it.
_MCP_SANDBOX_NETWORK_ISOLATION: Final[dict[str, int]] = {
    "none": 2,
    "bridge": 1,
    "host": 0,
}


def _network_isolation_rank(value: str) -> int | None:
    """Return the isolation rank of a sandbox network value, or ``None``."""
    return _MCP_SANDBOX_NETWORK_ISOLATION.get(normalize_identifier(value))


def _is_unlimited_cpus(value: str) -> bool:
    """Return whether a ``mcp_sandbox_cpus`` value removes the CPU quota."""
    try:
        return float(value) == 0
    except ValueError:
        # A malformed value is rejected downstream by the validator; do not
        # treat an unparseable quota as a weakening transition.
        return False


def _capability_patterns(raw: str | None) -> frozenset[str]:
    """Parse a comma-separated capability grant into its pattern set.

    Returns:
        The set of non-blank capability patterns.
    """
    if not raw:
        return frozenset()
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def _is_capability_widening(current: str | None, new: str) -> bool:
    """Return whether *new* grants a capability pattern *current* did not.

    Widening (guarded) is any pattern in *new* not already present in
    *current*: an empty-to-anything grant, ``"" -> "*"``, or adding a
    ``:write``. Narrowing (dropping patterns) is unguarded. A narrowing that
    happens to spell a more specific pattern than an existing wildcard is
    conservatively treated as widening: over-guarding never weakens the
    posture.

    Returns:
        ``True`` when the new grant introduces a pattern the current lacks.
    """
    return bool(_capability_patterns(new) - _capability_patterns(current))


def _is_mcp_sandbox_weakening(key: str, *, current: str | None, new: str) -> bool:
    """Return whether a ``tools.*`` MCP sandbox change relaxes isolation."""
    if key == _CREDENTIALED_MCP_ENABLED_KEY:
        # Default is "false" (off); enabling exposes credentialed actions.
        currently_off = current is None or not compare_ci(current, "true")
        return currently_off and compare_ci(new, "true")
    if key == _CREDENTIALED_MCP_CAPABILITIES_KEY:
        return _is_capability_widening(current, new)
    if key in _TOOL_FAMILY_ENABLED_KEYS:
        # Default is "false" (off); enabling exposes a destructive,
        # externally-reaching capability (a deploy release, a registry push).
        currently_off = current is None or not compare_ci(current, "true")
        return currently_off and compare_ci(new, "true")
    if key in _TOOL_FAMILY_TARGETS_KEYS:
        # Adding a target makes a real external destination reachable. Reuses
        # the capability-widening set difference: the shape (comma-separated
        # grant list, additions guarded, removals free) is identical.
        return _is_capability_widening(current, new)
    if key == _MCP_SANDBOX_ENABLED_KEY:
        currently_on = current is None or compare_ci(
            current, _MCP_SANDBOX_ENABLED_DEFAULT
        )
        return currently_on and not compare_ci(new, "true")
    if key == _MCP_SANDBOX_NETWORK_KEY:
        current_value = current if current is not None else _MCP_SANDBOX_NETWORK_DEFAULT
        new_rank = _network_isolation_rank(new)
        current_rank = _network_isolation_rank(current_value)
        if new_rank is None or current_rank is None:
            # An unrecognised value is rejected downstream by the validator; do
            # not treat it as a weakening transition here.
            return False
        # Any move toward less isolation (none -> bridge, none/bridge -> host)
        # is a weakening; the reverse (bridge -> none, host -> bridge) is not.
        return new_rank < current_rank
    if key == _MCP_SANDBOX_CPUS_KEY:
        current_unlimited = current is not None and _is_unlimited_cpus(current)
        return _is_unlimited_cpus(new) and not current_unlimited
    return False


def _exemption_keys(raw: str | None) -> frozenset[tuple[str, str, str]]:
    """Parse an ``exemptions`` JSON value into a set of scope keys.

    Reason text is ignored: two exemptions covering the same rule + scope are
    the same grant. A malformed / non-list value yields the empty set so a bad
    value is not treated as a broadening (the type validator rejects it).

    Returns:
        The set of ``(rule_id, scope_kind, match)`` keys.
    """
    if not raw:
        return frozenset()
    try:
        parsed = json.loads(raw)
    except ValueError, TypeError:
        return frozenset()
    if not isinstance(parsed, list):
        return frozenset()
    keys: set[tuple[str, str, str]] = set()
    for entry in parsed:
        if isinstance(entry, dict):
            keys.add(
                (
                    str(entry.get("rule_id", "")),
                    str(entry.get("scope_kind", "")),
                    str(entry.get("match", "")),
                )
            )
    return frozenset(keys)


def _is_output_style_weakening(key: str, *, current: str | None, new: str) -> bool:
    """Return whether an ``output_style.*`` change relaxes the guardrail."""
    if key == _OUTPUT_STYLE_ENABLED_KEY:
        currently_on = current is None or compare_ci(
            current, _OUTPUT_STYLE_ENABLED_DEFAULT
        )
        return currently_on and not compare_ci(new, "true")
    if key == _OUTPUT_STYLE_SHADOW_KEY:
        currently_off = current is None or not compare_ci(current, "true")
        return currently_off and compare_ci(new, "true")
    if key == _OUTPUT_STYLE_EXEMPTIONS_KEY:
        # Adding a sanctioned scope broadens what agents may legitimately emit;
        # removing / narrowing tightens and is unguarded.
        return bool(_exemption_keys(new) - _exemption_keys(current))
    if key == _OUTPUT_STYLE_PACK_KEY:
        # A pack swap can replace the whole rule set; without loading both packs
        # the write path cannot prove the new pack is not more permissive, so any
        # actual change to the active pack is treated as weakening. An unset
        # current value resolves to the default pack, so the first switch away
        # from it is guarded too.
        effective_current = (
            current if current is not None else _OUTPUT_STYLE_PACK_DEFAULT
        )
        return not compare_ci(effective_current, new)
    return False


def _is_engine_weakening(key: str, *, current: str | None, new: str) -> bool:
    """Return whether an ``engine.*`` oracle change relaxes verification."""
    if key == _ENGINE_ORACLE_DISABLE_KEY:
        currently_on = current is None or compare_ci(
            current, _ENGINE_ORACLE_ENABLED_DEFAULT
        )
        return currently_on and not compare_ci(new, "true")
    if key == _ENGINE_ORACLE_SHADOW_KEY:
        currently_off = current is None or not compare_ci(current, "true")
        return currently_off and compare_ci(new, "true")
    if key == _ENGINE_ORACLE_MIN_STAKES_KEY:
        current_stakes = Stakes(current) if current is not None else Stakes.LOW
        try:
            new_stakes = Stakes(new)
        except ValueError:
            # A malformed value is rejected downstream by the type validator;
            # do not treat an unparseable stakes as a weakening transition.
            return False
        return compare_stakes(new_stakes, current_stakes) > 0
    return False


def _is_providers_weakening(key: str, *, current: str | None, new: str) -> bool:
    """Return whether a ``providers.*`` change relaxes posture."""
    if key == _GATEWAY_ENABLED_KEY:
        # Default is "false" (off); enabling opens the egress path.
        currently_off = current is None or not compare_ci(current, "true")
        return currently_off and compare_ci(new, "true")
    return False


class SettingsWriteGovernance(BaseModel):
    """Operator deliberate-action context for a guarded settings write.

    A security-weakening transition requires ``confirm=True`` plus a
    non-blank ``reason`` and ``actor``. The enable / tighten direction does
    not consult this object.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    confirm: bool = False
    reason: str = ""
    actor: str = ""

    @property
    def is_satisfied(self) -> bool:
        """Whether this governance authorises a weakening transition."""
        return self.confirm and bool(self.reason.strip()) and bool(self.actor.strip())


def _is_guarded(namespace: str, key: str) -> bool:
    """Return whether ``(namespace, key)`` is a governed weakening candidate."""
    if namespace == _SECURITY_NS:
        return key in _WEAKENING_BOOL_KEYS or key == _OUTPUT_SCAN_POLICY_KEY
    if namespace == _ENGINE_NS:
        return key in _ENGINE_GUARDED_KEYS
    if namespace == _TOOLS_NS:
        return key in _MCP_SANDBOX_GUARDED_KEYS
    if namespace == _OUTPUT_STYLE_NS:
        return key in _OUTPUT_STYLE_GUARDED_KEYS
    if namespace == _PROVIDERS_NS:
        return key == _GATEWAY_ENABLED_KEY
    return False


def _is_weakening(namespace: str, key: str, *, current: str | None, new: str) -> bool:
    """Return whether ``current -> new`` weakens the posture for *namespace.key*."""
    if namespace == _PROVIDERS_NS:
        return _is_providers_weakening(key, current=current, new=new)
    if namespace == _ENGINE_NS:
        return _is_engine_weakening(key, current=current, new=new)
    if namespace == _TOOLS_NS:
        return _is_mcp_sandbox_weakening(key, current=current, new=new)
    if namespace == _OUTPUT_STYLE_NS:
        return _is_output_style_weakening(key, current=current, new=new)
    if key in _WEAKENING_BOOL_KEYS:
        # Weakening only when turning a currently-enabled toggle off. A
        # missing current value (first write) is treated as the registered
        # default "true", so an explicit first write of "false" is guarded.
        currently_on = current is None or compare_ci(current, "true")
        return currently_on and not compare_ci(new, "true")
    if key == _OUTPUT_SCAN_POLICY_KEY:
        new_permissive = compare_ci(new, _PERMISSIVE_OUTPUT_SCAN_POLICY)
        current_permissive = current is not None and compare_ci(
            current, _PERMISSIVE_OUTPUT_SCAN_POLICY
        )
        return new_permissive and not current_permissive
    return False


async def enforce_security_write_governance(
    items: Sequence[tuple[str, str, str]],
    *,
    governance: SettingsWriteGovernance | None,
    get_current: Callable[[str, str], Awaitable[str | None]],
) -> None:
    """Reject a security-weakening write that lacks the deliberate guardrail.

    Iterates *items*; for each security toggle whose ``current -> new``
    transition weakens posture, requires *governance* to be satisfied
    (``confirm`` + ``reason`` + ``actor``). ``get_current`` resolves the
    current stored value (``None`` when unset).

    Raises:
        SecurityToggleConfirmationRequiredError: If any weakening transition
            is not authorised by a satisfied *governance*.
    """
    for namespace, key, value in items:
        if not _is_guarded(namespace, key):
            continue
        current = await get_current(namespace, key)
        if not _is_weakening(namespace, key, current=current, new=value):
            continue
        if governance is not None and governance.is_satisfied:
            logger.info(
                SETTINGS_SECURITY_GOVERNANCE_CONFIRMED,
                namespace=namespace,
                key=key,
                note="security-weakening transition confirmed",
                actor=governance.actor,
                reason=governance.reason,
            )
            continue
        logger.warning(
            SETTINGS_VALIDATION_FAILED,
            namespace=namespace,
            key=key,
            note="security-weakening transition rejected (no confirm+reason)",
        )
        msg = (
            f"Weakening setting {namespace}.{key} relaxes the running"
            " security / verification posture and requires the deliberate"
            " path carrying an explicit confirm + reason + actor; a generic"
            " settings write cannot disable or relax it."
        )
        raise SecurityToggleConfirmationRequiredError(msg)


async def guard_security_writes(
    items: Sequence[tuple[str, str, str]],
    *,
    governance: SettingsWriteGovernance | None,
    get_entry: Callable[[str, str], Awaitable[SettingValue]],
) -> None:
    """Enforce the weakening guard, resolving current values via *get_entry*.

    Adapts a raising resolver (e.g. ``SettingsService.get``) into the
    ``None``-on-unresolved contract :func:`enforce_security_write_governance`
    expects, so the service write path is a single call. An unresolved key
    (e.g. unset, backend hiccup) compares against the registered default
    inside the guard.
    """

    async def _current(namespace: str, key: str) -> str | None:
        try:
            entry = await get_entry(namespace, key)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            return None
        return entry.value

    await enforce_security_write_governance(
        items, governance=governance, get_current=_current
    )


async def guard_security_delete(
    namespace: str,
    definitions: Iterable[SettingDefinition],
    *,
    resolve_fallback: Callable[[SettingDefinition], Awaitable[SettingValue]],
    get_entry: Callable[[str, str], Awaitable[SettingValue]],
) -> None:
    """Hard-block a delete that would weaken a governed setting.

    Deleting an override reverts the key to its env > default fallback, so a
    delete that would drop a currently-secure toggle to a weaker effective
    value must go through the explicit confirm+reason set path, never a silent
    delete. This holds across every governed namespace (``security``,
    ``engine``, ``tools``, ``output_style``, ``providers``): deleting, say, the
    ``tools.credentialed_mcp_enabled`` or ``providers.gateway_enabled``
    override would otherwise revert to a broader env/default value, bypassing
    the set-path guardrail. The guarded value is the real env>default fallback
    (resolved via *resolve_fallback*), not the bare code default, so a
    weakening env override is not missed. ``governance=None`` is passed so a
    weakening delete is hard-blocked rather than confirmable inline.
    """
    items = [
        (namespace, definition.key, (await resolve_fallback(definition)).value)
        for definition in definitions
    ]
    await guard_security_writes(items, governance=None, get_entry=get_entry)
