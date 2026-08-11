# module-kind: declarative
"""Which settings writes weaken the running posture, and in which direction.

The judgement half of the write guardrail: given ``(namespace, key)`` and a
``current -> new`` transition, decide whether the change relaxes security,
verification, isolation, anti-abuse or evidence retention. It answers only
that; who is allowed to make such a change, and what they must supply to do
it, lives in :mod:`synthorg.settings.write_governance`.

The split is the cohesion boundary the two halves already had. This half is a
per-namespace policy table that grows with every setting that becomes live;
the other is one enforcement loop that does not.
"""

import json
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from synthorg.core.normalization import compare_ci, normalize_identifier
from synthorg.core.task_enums import Stakes, compare_stakes
from synthorg.settings.enums import SettingNamespace

_SECURITY_NS: Final[str] = SettingNamespace.SECURITY.value
_ENGINE_NS: Final[str] = SettingNamespace.ENGINE.value
_TOOLS_NS: Final[str] = SettingNamespace.TOOLS.value
_OUTPUT_STYLE_NS: Final[str] = SettingNamespace.OUTPUT_STYLE.value
_PROVIDERS_NS: Final[str] = SettingNamespace.PROVIDERS.value
_INTEGRATIONS_NS: Final[str] = SettingNamespace.INTEGRATIONS.value
_API_NS: Final[str] = SettingNamespace.API.value
_SELF_IMPROVEMENT_NS: Final[str] = SettingNamespace.SELF_IMPROVEMENT.value
_STRATEGY_NS: Final[str] = SettingNamespace.STRATEGY.value

# Webhook-receipt retention. Shortening the window destroys inbound-delivery
# evidence on the next sweep, and the destruction is irreversible, so the
# shortening direction routes through the deliberate guardrail. Lengthening it,
# or setting the never-sweep value, retains strictly more and is unguarded.
_WEBHOOK_RETENTION_KEY: Final[str] = "webhook_receipt_retention_days"
# The registered default, and the value that means "never sweep": a window of
# zero days would otherwise read as "discard everything immediately", which is
# the opposite of what it does.
_RETENTION_NEVER_SWEEP: Final[str] = "0"

# Enabling the LLM gateway opens an OpenAI-compatible egress path that lets an
# embedded harness make provider calls, so the ``false -> true`` transition is
# the weakening direction; disabling it (closing the egress) tightens and is
# unguarded. It ships ON, so an unset value is already the running posture:
# see _is_default_on_capability_reenable for what that makes a decision.
_GATEWAY_ENABLED_KEY: Final[str] = "gateway_enabled"

# Shipping the OpenHands loop means an egress-pinned container reaching the
# gateway and the credentialed-MCP surface, so re-enabling it after an explicit
# disable is guarded on the same terms as the gateway itself.
_OPENHANDS_ENABLED_KEY: Final[str] = "openhands_enabled"

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
    {"enabled", "audit_enabled", "post_tool_scanning_enabled", "tls_verify"}
)

# Token entropy for the auth surface: session tickets, password-reset and
# refresh tokens, OAuth state. Narrowing it makes every token minted afterwards
# easier to guess, so the shrinking direction is the weakening one. The hard
# floor lives in ``core.auth.token_size``; this guards the deliberate step, not
# the range.
_AUTH_TOKEN_BYTES_KEY: Final[str] = "auth_token_bytes"  # noqa: S105
_AUTH_TOKEN_BYTES_DEFAULT: Final[str] = "32"  # noqa: S105

# The agent middleware chain carries the authority-deference defence: a
# justification header injected when a conversation shows authority cues. Off
# is a prompt-injection countermeasure removed, so it guards like a security
# toggle rather than a performance knob.
_ENGINE_MIDDLEWARE_KEY: Final[str] = "enable_agent_middleware"
_ENGINE_MIDDLEWARE_DEFAULT: Final[str] = "true"

# Letting the meta-loop propose changes to its own source is the widest blast
# radius the product has, so the enabling direction is guarded. The credential
# requirement in the loader is a functional gate, not a deliberate-action one.
_CODE_MODIFICATION_KEY: Final[str] = "code_modification_enabled"

# Consensus-velocity detection and the premortem are the org's only checks on
# a meeting agreeing too quickly and on a decision leaving unexamined failure
# modes: the meeting-level counterpart of the completion oracle and the ask
# policy, which are guarded for exactly this reason. Relaxing either removes
# independent scrutiny from every subsequent meeting, and a threshold of 1.0
# silences the detector without disabling anything by name.
_STRATEGY_CONSENSUS_THRESHOLD_KEY: Final[str] = "consensus_velocity_threshold"
_STRATEGY_PREMORTEM_PARTICIPANTS_KEY: Final[str] = "premortem_participants"
_STRATEGY_THRESHOLD_DEFAULT: Final[float] = 0.85
_STRATEGY_PREMORTEM_DEFAULT: Final[str] = "all"
# Ordered by how much scrutiny each admits, so a move down the ranking is a
# weakening and a move up is not. ``consensus_velocity_action`` is deliberately
# absent: its three actions have no such ordering.
_PREMORTEM_SCRUTINY_RANK: Final[Mapping[str, int]] = MappingProxyType(
    {"none": 0, "strategic": 1, "all": 2}
)
_STRATEGY_GUARDED_KEYS: Final[frozenset[str]] = frozenset(
    {_STRATEGY_CONSENSUS_THRESHOLD_KEY, _STRATEGY_PREMORTEM_PARTICIPANTS_KEY}
)

# The global rate limiter is the anti-abuse boundary in front of the whole API.
# Turning it off, raising any tier's budget, or shortening the window each admit
# more traffic than before. The credential-endpoint cap is the brute-force bound
# on the login, setup and change-password routes, which is why raising it is
# guarded on the same terms as disabling the limiter outright.
_RATE_LIMITER_ENABLED_KEY: Final[str] = "rate_limiter_enabled"
_RATE_LIMITER_ENABLED_DEFAULT: Final[str] = "true"
_RATE_LIMIT_TIME_UNIT_KEY: Final[str] = "rate_limit_time_unit"
_RATE_LIMIT_TIME_UNIT_DEFAULT: Final[str] = "minute"
# Registered defaults, consulted when a key is unset so a first explicit
# widening write is guarded rather than waved through for lack of a prior value.
_RATE_LIMIT_CAP_DEFAULTS: Final[dict[str, str]] = {
    "rate_limit_floor_max_requests": "10000",
    "rate_limit_unauth_max_requests": "20",
    "rate_limit_auth_max_requests": "6000",
    "rate_limit_auth_endpoint_max_requests": "10",
}
_RATE_LIMIT_CAP_KEYS: Final[frozenset[str]] = frozenset(_RATE_LIMIT_CAP_DEFAULTS)
# Window length in seconds. The same cap over a shorter window admits
# proportionally more traffic, so shortening is the weakening direction.
_RATE_LIMIT_WINDOW_SECONDS: Final[dict[str, int]] = {
    "second": 1,
    "minute": 60,
    "hour": 3600,
    "day": 86400,
}
_API_GUARDED_KEYS: Final[frozenset[str]] = (
    frozenset({_RATE_LIMITER_ENABLED_KEY, _RATE_LIMIT_TIME_UNIT_KEY})
    | _RATE_LIMIT_CAP_KEYS
)
# The permissive output-scan policy: switching TO it weakens posture.
_OUTPUT_SCAN_POLICY_KEY: Final[str] = "output_scan_policy_type"
_PERMISSIVE_OUTPUT_SCAN_POLICY: Final[str] = "log_only"
# Non-boolean security keys whose value, not merely its truthiness, decides
# the direction.
_SECURITY_VALUE_KEYS: Final[frozenset[str]] = frozenset(
    {_OUTPUT_SCAN_POLICY_KEY, _AUTH_TOKEN_BYTES_KEY}
)

# Completion-oracle keys in the ``engine`` namespace that relax independent
# verification. Disabling the oracle, switching it to shadow mode (every REJECT
# becomes a logged no-op), or raising the stakes floor so fewer tasks are
# reviewed each drop the running verification posture, so they route through
# the same deliberate confirm+reason+actor guardrail as the security toggles.
_ENGINE_ORACLE_DISABLE_KEY: Final[str] = "completion_oracle_enabled"
_ENGINE_ORACLE_SHADOW_KEY: Final[str] = "completion_oracle_shadow_mode"
_ENGINE_ORACLE_MIN_STAKES_KEY: Final[str] = "completion_oracle_min_stakes"
# Loop-routing keys in the ``engine`` namespace. Shipping a sandboxed coding
# loop and routing real tasks through one are different decisions with very
# different blast radius: these three are what actually spawn a container that
# executes attacker-influenceable prompts against the agent's workspace, so
# they take the same deliberate guardrail as the capability toggles rather
# than a lighter one than them.
_ENGINE_AUTO_SELECT_KEY: Final[str] = "loop_auto_select_enabled"
_ENGINE_DEFAULT_LOOP_KEY: Final[str] = "default_loop_type"
_ENGINE_COMPLEXITY_OVERRIDES_KEY: Final[str] = "loop_complexity_overrides"
_SANDBOXED_LOOP_TYPE: Final[str] = "openhands"

# The standing ask directive and the two tools that carry a question are the
# only in-run path by which an agent defers a material, hard-to-reverse choice
# to a human. Turning any of them off removes that deferral, which relaxes the
# running verification posture the same way disabling the completion oracle
# does, so the disabling direction routes through the deliberate guardrail.
_ENGINE_ASK_POLICY_KEY: Final[str] = "ask_policy_enabled"
_ENGINE_CLARIFICATION_KEY: Final[str] = "clarification_enabled"
_ENGINE_SCOPING_KEY: Final[str] = "scoping_enabled"
_ENGINE_ASK_KEYS: Final[frozenset[str]] = frozenset(
    {_ENGINE_ASK_POLICY_KEY, _ENGINE_CLARIFICATION_KEY, _ENGINE_SCOPING_KEY}
)
_ENGINE_ASK_ENABLED_DEFAULT: Final[str] = "true"

# The extra-directives list is guarded in the ADDING direction. An addition
# cannot delete the standing directive, but it does not need to: it renders
# directly beneath it in the same section, so "never escalate schema or spend
# decisions, decide them yourself" neutralises the deferral posture org-wide
# for every agent. That is the same weakening the guarded enable toggle
# protects, reached by a different edit. Shrinking or clearing the list only
# leaves the standing directive, so it is not guarded.
_ENGINE_ASK_EXTRA_DIRECTIVES_KEY: Final[str] = "ask_policy_extra_directives"

_ENGINE_GUARDED_KEYS: Final[frozenset[str]] = frozenset(
    {
        _ENGINE_ORACLE_DISABLE_KEY,
        _ENGINE_ORACLE_SHADOW_KEY,
        _ENGINE_ORACLE_MIN_STAKES_KEY,
        _ENGINE_MIDDLEWARE_KEY,
        _ENGINE_AUTO_SELECT_KEY,
        _ENGINE_DEFAULT_LOOP_KEY,
        _ENGINE_COMPLEXITY_OVERRIDES_KEY,
        _ENGINE_ASK_EXTRA_DIRECTIVES_KEY,
        *_ENGINE_ASK_KEYS,
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
_TOOLS_GUARDED_KEYS: Final[frozenset[str]] = frozenset(
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
        _OPENHANDS_ENABLED_KEY,
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


def _is_default_on_capability_reenable(current: str | None, new: str) -> bool:
    """Return whether an explicitly-disabled default-on capability is re-enabled.

    An unset current resolves to the registered default (on), so the first
    write of ``true`` restates what is already running rather than opening
    anything. Only a stored ``false`` returning to ``true`` reopens the path.

    Returns:
        ``True`` when the transition reopens the capability.
    """
    currently_off = current is not None and not compare_ci(current, "true")
    return currently_off and compare_ci(new, "true")


def _is_tools_weakening(key: str, *, current: str | None, new: str) -> bool:
    """Return whether a ``tools.*`` change relaxes isolation or blast radius."""
    if key == _OPENHANDS_ENABLED_KEY:
        return _is_default_on_capability_reenable(current, new)
    if key == _CREDENTIALED_MCP_ENABLED_KEY:
        return _is_default_on_capability_reenable(current, new)
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


def _as_int(value: str) -> int | None:
    """Return *value* as an integer, or ``None`` when it does not parse.

    Returns:
        The parsed integer, or ``None``. A malformed value is rejected
        downstream by the type validator, so it is not a weakening transition.
    """
    try:
        return int(value)
    except ValueError:
        return None


def _is_api_weakening(key: str, *, current: str | None, new: str) -> bool:
    """Return whether an ``api.*`` rate-limit change admits more traffic."""
    if key == _RATE_LIMITER_ENABLED_KEY:
        currently_on = current is None or compare_ci(
            current, _RATE_LIMITER_ENABLED_DEFAULT
        )
        return currently_on and not compare_ci(new, "true")
    if key == _RATE_LIMIT_TIME_UNIT_KEY:
        effective = current if current is not None else _RATE_LIMIT_TIME_UNIT_DEFAULT
        current_window = _RATE_LIMIT_WINDOW_SECONDS.get(normalize_identifier(effective))
        new_window = _RATE_LIMIT_WINDOW_SECONDS.get(normalize_identifier(new))
        if current_window is None or new_window is None:
            return False
        return new_window < current_window
    if key in _RATE_LIMIT_CAP_KEYS:
        effective = current if current is not None else _RATE_LIMIT_CAP_DEFAULTS[key]
        current_cap = _as_int(effective)
        new_cap = _as_int(new)
        if current_cap is None or new_cap is None:
            return False
        return new_cap > current_cap
    return False


def _is_self_improvement_weakening(key: str, *, current: str | None, new: str) -> bool:
    """Return whether a ``self_improvement.*`` change widens blast radius."""
    if key != _CODE_MODIFICATION_KEY:
        return False
    # Default is "false" (off); enabling lets the meta-loop propose changes to
    # its own source.
    currently_off = current is None or not compare_ci(current, "true")
    return currently_off and compare_ci(new, "true")


def _sandboxed_loop_routes(value: str | None) -> frozenset[str]:
    """Return which routes in a loop-routing value name the sandboxed loop.

    Compares routes rather than mere presence: a value that already routes one
    complexity to the sandboxed loop and now routes a second is widening what
    reaches it, which a "does the string mention it" test would wave through
    because the answer was already yes.

    Covers both shapes the routing keys use: a bare loop type (which yields the
    single empty-named route) and the ``complexity:loop`` override list.

    Returns:
        The complexity names routed to the sandboxed loop.
    """
    if not value:
        return frozenset()
    routes = set()
    for entry in value.lower().split(","):
        complexity, _, loop = entry.strip().rpartition(":")
        if loop.strip() == _SANDBOXED_LOOP_TYPE:
            routes.add(complexity.strip())
    return frozenset(routes)


def _has_new_directive(current: str | None, new: str) -> bool:
    """Return whether *new* carries an entry absent from *current*.

    Compares entry sets, not list length. A write that drops one directive and
    adds a different one keeps the count identical, and it is the arriving
    entry, not the net count, that can neutralise the deferral posture.

    Returns:
        Whether any entry is new, or ``False`` when either value does not
        parse as a JSON array. A malformed payload is rejected by the type
        validator, so treating it as no addition here does not let one through.
    """

    def _entries(raw: str | None) -> set[str] | None:
        if raw is None or not raw.strip():
            return set()
        try:
            parsed = json.loads(raw)
        except ValueError, TypeError:
            return None
        if not isinstance(parsed, list):
            return None
        # Canonical per-entry text, because a directive is a dict and a dict
        # is unhashable; sorting the keys makes a reordered but identical
        # entry compare equal rather than reading as an addition.
        return {json.dumps(entry, sort_keys=True) for entry in parsed}

    before, after = _entries(current), _entries(new)
    if before is None or after is None:
        return False
    return bool(after - before)


def _is_engine_weakening(key: str, *, current: str | None, new: str) -> bool:
    """Return whether an ``engine.*`` oracle, middleware or ask change relaxes it."""
    if key == _ENGINE_ASK_EXTRA_DIRECTIVES_KEY:
        return _has_new_directive(current, new)
    if key in _ENGINE_ASK_KEYS:
        currently_on = current is None or compare_ci(
            current, _ENGINE_ASK_ENABLED_DEFAULT
        )
        return currently_on and not compare_ci(new, "true")
    if key == _ENGINE_MIDDLEWARE_KEY:
        currently_on = current is None or compare_ci(
            current, _ENGINE_MIDDLEWARE_DEFAULT
        )
        return currently_on and not compare_ci(new, "true")
    if key == _ENGINE_ORACLE_DISABLE_KEY:
        currently_on = current is None or compare_ci(
            current, _ENGINE_ORACLE_ENABLED_DEFAULT
        )
        return currently_on and not compare_ci(new, "true")
    if key == _ENGINE_ORACLE_SHADOW_KEY:
        currently_off = current is None or not compare_ci(current, "true")
        return currently_off and compare_ci(new, "true")
    if key == _ENGINE_AUTO_SELECT_KEY:
        currently_off = current is None or not compare_ci(current, "true")
        return currently_off and compare_ci(new, "true")
    if key in (_ENGINE_DEFAULT_LOOP_KEY, _ENGINE_COMPLEXITY_OVERRIDES_KEY):
        # Naming the sandboxed loop where it was not named before is the
        # weakening: it starts routing real tasks into a container that runs
        # generated code. Removing it tightens and stays unguarded.
        added = _sandboxed_loop_routes(new) - _sandboxed_loop_routes(current)
        return bool(added)
    if key == _ENGINE_ORACLE_MIN_STAKES_KEY:
        # A stored or env-overridden value can be malformed too, and raising
        # here would fail the write with a parse error instead of judging the
        # transition. The lowest floor is the safe reading: it makes any real
        # raise compare as a weakening rather than skipping the check.
        current_stakes = _as_stakes(current) or Stakes.LOW
        try:
            new_stakes = Stakes(new)
        except ValueError:
            # A malformed value is rejected downstream by the type validator;
            # do not treat an unparseable stakes as a weakening transition.
            return False
        return compare_stakes(new_stakes, current_stakes) > 0
    return False


def _as_stakes(value: str | None) -> Stakes | None:
    """Return *value* as a stakes level, or ``None`` when it does not parse.

    Args:
        value: The stored or incoming stakes string.

    Returns:
        The parsed level, or ``None``.
    """
    if value is None:
        return None
    try:
        return Stakes(value)
    except ValueError:
        return None


def _is_integrations_weakening(key: str, *, current: str | None, new: str) -> bool:
    """Return whether an ``integrations.*`` change shortens evidence retention."""
    if key != _WEBHOOK_RETENTION_KEY:
        return False
    effective_current = current if current is not None else _RETENTION_NEVER_SWEEP
    new_days = _as_int(new)
    current_days = _as_int(effective_current)
    if new_days is None or current_days is None:
        return False
    if new_days == 0:
        # Never-sweep retains everything, whatever the previous window was.
        return False
    # A finite window against never-sweep starts discarding what was kept
    # indefinitely; against a longer window it discards the difference.
    return current_days == 0 or new_days < current_days


def _is_providers_weakening(key: str, *, current: str | None, new: str) -> bool:
    """Return whether a ``providers.*`` change relaxes posture."""
    if key == _GATEWAY_ENABLED_KEY:
        return _is_default_on_capability_reenable(current, new)
    return False


def is_guarded(namespace: str, key: str) -> bool:
    """Return whether ``(namespace, key)`` is a governed weakening candidate."""
    if namespace == _SECURITY_NS:
        return key in _WEAKENING_BOOL_KEYS or key in _SECURITY_VALUE_KEYS
    if namespace == _ENGINE_NS:
        return key in _ENGINE_GUARDED_KEYS
    if namespace == _TOOLS_NS:
        return key in _TOOLS_GUARDED_KEYS
    if namespace == _OUTPUT_STYLE_NS:
        return key in _OUTPUT_STYLE_GUARDED_KEYS
    if namespace == _PROVIDERS_NS:
        return key == _GATEWAY_ENABLED_KEY
    if namespace == _INTEGRATIONS_NS:
        return key == _WEBHOOK_RETENTION_KEY
    if namespace == _API_NS:
        return key in _API_GUARDED_KEYS
    if namespace == _SELF_IMPROVEMENT_NS:
        return key == _CODE_MODIFICATION_KEY
    if namespace == _STRATEGY_NS:
        return key in _STRATEGY_GUARDED_KEYS
    return False


def _is_security_weakening(key: str, *, current: str | None, new: str) -> bool:
    """Return whether a ``security.*`` change relaxes posture."""
    if key == _AUTH_TOKEN_BYTES_KEY:
        effective = current if current is not None else _AUTH_TOKEN_BYTES_DEFAULT
        current_width = _as_int(effective)
        new_width = _as_int(new)
        if current_width is None or new_width is None:
            return False
        return new_width < current_width
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


def _is_strategy_weakening(key: str, *, current: str | None, new: str) -> bool:
    """Return whether a ``strategy.*`` change relaxes meeting scrutiny.

    Args:
        key: The setting key being written.
        current: The stored value, or ``None`` when unset.
        new: The incoming value.

    Returns:
        ``True`` when the write reduces independent scrutiny.
    """
    if key == _STRATEGY_CONSENSUS_THRESHOLD_KEY:
        # Raising the bar for "these positions converged too fast" means
        # fewer meetings are ever flagged, and 1.0 silences the detector
        # outright. Same direction as the oracle's min-stakes floor.
        current_value = _as_threshold(current)
        new_value = _as_threshold(new)
        if new_value is None:
            # Malformed values are rejected by the type validator; do not
            # read one as a weakening transition.
            return False
        return new_value > (current_value or _STRATEGY_THRESHOLD_DEFAULT)
    if key == _STRATEGY_PREMORTEM_PARTICIPANTS_KEY:
        current_rank = _PREMORTEM_SCRUTINY_RANK.get(
            current or _STRATEGY_PREMORTEM_DEFAULT
        )
        new_rank = _PREMORTEM_SCRUTINY_RANK.get(new)
        if new_rank is None or current_rank is None:
            return False
        return new_rank < current_rank
    return False


def _as_threshold(value: str | None) -> float | None:
    """Return *value* as a threshold, or ``None`` when it does not parse.

    Args:
        value: The stored or incoming threshold string.

    Returns:
        The parsed threshold, or ``None``.
    """
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def is_weakening(namespace: str, key: str, *, current: str | None, new: str) -> bool:
    """Return whether ``current -> new`` weakens the posture for *namespace.key*."""
    if namespace == _STRATEGY_NS:
        return _is_strategy_weakening(key, current=current, new=new)
    if namespace == _PROVIDERS_NS:
        return _is_providers_weakening(key, current=current, new=new)
    if namespace == _INTEGRATIONS_NS:
        return _is_integrations_weakening(key, current=current, new=new)
    if namespace == _ENGINE_NS:
        return _is_engine_weakening(key, current=current, new=new)
    if namespace == _TOOLS_NS:
        return _is_tools_weakening(key, current=current, new=new)
    if namespace == _OUTPUT_STYLE_NS:
        return _is_output_style_weakening(key, current=current, new=new)
    if namespace == _API_NS:
        return _is_api_weakening(key, current=current, new=new)
    if namespace == _SELF_IMPROVEMENT_NS:
        return _is_self_improvement_weakening(key, current=current, new=new)
    return _is_security_weakening(key, current=current, new=new)
