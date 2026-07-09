# module-kind: code
"""Overlay individual feature settings onto the self-improvement config.

The self-improvement and Chief-of-Staff feature flags and per-feature
models are individual runtime settings (namespaces ``self_improvement``
and ``chief_of_staff``), so the wizard and dashboard Settings can toggle
them over the standard ``/settings`` API. They are the single source of
truth: this overlay applies them onto the dict parsed from the
``meta.self_improvement`` structural blob, always winning over any flag
value the blob carries. Only the deep structural tuning (schedule,
rollout, regression, guards, toolsmith internals) is sourced from the
blob.
"""

import json
from typing import cast

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.normalization import normalize_ascii_lowercase
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meta import (
    META_SELF_IMPROVEMENT_LOAD_FAILED,
    META_TOOLSMITH_ALLOWLIST_REQUIRED,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.service_protocol import SettingsServiceProtocol

logger = get_logger(__name__)

_TRUE_TOKENS = frozenset({"true", "1", "yes", "on"})

# self_improvement namespace boolean keys that map 1:1 onto
# SelfImprovementConfig fields of the same name.
_SI_BOOL_FIELDS: tuple[str, ...] = (
    "enabled",
    "chief_of_staff_enabled",
    "config_tuning_enabled",
    "architecture_proposals_enabled",
    "prompt_tuning_enabled",
    "code_modification_enabled",
    "tool_creation_enabled",
)

# chief_of_staff setting key -> ChiefOfStaffConfig field name. The chat
# capability uses the settings/UI key ``explain_chat_enabled`` (so it reads
# distinctly from the self-improvement ``chief_of_staff_enabled`` switch),
# but the config field it maps to is ``chat_enabled``. Every other flag
# shares its name between the setting and the config field.
_COS_BOOL_FIELDS: dict[str, str] = {
    "explain_chat_enabled": "chat_enabled",
    "propose_enabled": "propose_enabled",
    "routing_enabled": "routing_enabled",
    "group_chat_enabled": "group_chat_enabled",
    "learning_enabled": "learning_enabled",
    "alerts_enabled": "alerts_enabled",
    "narrative_enabled": "narrative_enabled",
    "invite_enabled": "invite_enabled",
    "direct_mcp_enabled": "direct_mcp_enabled",
}

# chief_of_staff per-feature model keys; each shares its name between the
# setting and the ChiefOfStaffConfig field. (invite + direct-mcp have no
# per-feature model: group invites reuse each agent's identity model, and
# direct-MCP acting runs under the agent's own model.)
_COS_MODEL_FIELDS: tuple[str, ...] = (
    "chat_model",
    "propose_model",
    "routing_model",
    "narrative_model",
)

# charter setting key -> CharterConfig field (shared names). The model is
# skip-if-blank (an unset model keeps the config's blank default so the
# feature reports "not configured" rather than resolving a placeholder); the
# scalar tuning fields overlay whenever present so a `/settings` change reaches
# the boot CharterConfig, not just the per-turn live-resolve path.
_CHARTER_MODEL_FIELDS: tuple[str, ...] = ("interview_model",)
_CHARTER_SCALAR_FIELDS: tuple[str, ...] = (
    "interview_temperature",
    "interview_max_tokens",
    "interview_max_turns",
    "default_currency",
)


def _as_bool(value: str) -> bool:
    """Coerce a stored setting string to a boolean.

    Returns:
        ``True`` when *value* is a recognised true token.
    """
    return normalize_ascii_lowercase(value) in _TRUE_TOKENS


def _parse_capability_list(raw: str) -> list[str]:
    """Parse the JSON-encoded toolsmith capability allowlist setting.

    Returns:
        The list of non-blank ``domain:action`` capability tags, or ``[]``
        when the setting is unset or malformed (treated as deny-all rather
        than crashing the overlay).
    """
    text = raw.strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError) as exc:
        # A malformed allowlist must not crash the overlay, but silently
        # returning ``[]`` would be indistinguishable from "never set" to an
        # operator who wrote invalid JSON, so log the discarded value.
        logger.warning(
            META_SELF_IMPROVEMENT_LOAD_FAILED,
            reason="tool_creation_allowed_capabilities_parse_error",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return []
    if not isinstance(parsed, list):
        logger.warning(
            META_SELF_IMPROVEMENT_LOAD_FAILED,
            reason="tool_creation_allowed_capabilities_not_a_list",
        )
        return []
    # Only real strings are capability tags; coercing non-strings (``[true]``,
    # ``[0]``, ``[{}]``) would yield truthy entries that silently enable tool
    # creation, so drop them and surface the discard to the operator.
    valid = [item.strip() for item in parsed if isinstance(item, str) and item.strip()]
    non_string = sum(1 for item in parsed if not isinstance(item, str))
    if non_string:
        logger.warning(
            META_SELF_IMPROVEMENT_LOAD_FAILED,
            reason="tool_creation_allowed_capabilities_non_string_items",
            dropped=non_string,
        )
    return valid


def _nested(overrides: dict[str, object], key: str) -> dict[str, object]:
    """Return the nested sub-config dict for *key*, creating it if absent.

    Returns:
        A mutable dict stored under ``overrides[key]``.
    """
    nested = overrides.get(key)
    if not isinstance(nested, dict):
        nested = {}
        overrides[key] = nested
    return cast("dict[str, object]", nested)


async def overlay_feature_settings(
    settings_service: SettingsServiceProtocol,
    overrides: dict[str, object],
) -> dict[str, object]:
    """Overlay the individual feature settings onto *overrides*.

    Reads the ``self_improvement`` and ``chief_of_staff`` namespaces and
    writes each flag/model onto the dict that will construct
    :class:`~synthorg.meta.config.SelfImprovementConfig`. Settings always
    win over any value already present in *overrides* (the structural
    blob). Blank model strings are skipped so the config keeps its
    built-in non-blank default until setup auto-selects a real model.

    Args:
        settings_service: The application's settings service.
        overrides: The dict parsed from the structural ``meta.self_improvement``
            blob; mutated in place and returned.

    Returns:
        The overlaid ``overrides`` dict.
    """
    # Read each namespace independently: a failure on one still applies the
    # other's overlay (best-partial), and the log names the failing
    # namespace instead of a single opaque "settings read error".
    si = await _read_namespace(settings_service, SettingNamespace.SELF_IMPROVEMENT)
    cos = await _read_namespace(settings_service, SettingNamespace.CHIEF_OF_STAFF)

    for key in _SI_BOOL_FIELDS:
        if key in si:
            overrides[key] = _as_bool(si[key])
    # tool_creation_enabled must agree with toolsmith.enabled (the
    # SelfImprovementConfig cross-field validator), and the toolsmith
    # rejects an empty allowlist (deny-all). Overlay the allowlist and, when
    # tool creation is requested without one, hold it off rather than letting
    # the invalid sub-config sink the whole self-improvement posture.
    if "tool_creation_enabled" in overrides:
        allowlist = _parse_capability_list(
            si.get("tool_creation_allowed_capabilities", ""),
        )
        requested = bool(overrides["tool_creation_enabled"])
        enabled = requested and bool(allowlist)
        if requested and not allowlist:
            # DEBUG, not WARNING: this is an expected, derivable quiescent
            # state (toolsmith requested but no allowed_capabilities set),
            # re-evaluated on every overlay rebuild (boot, a wiring pass, or a
            # self_improvement / chief_of_staff settings edit), so at WARNING it
            # tiles the log. The held-off state is already visible in the
            # feature's own status. (The sibling malformed-value branches stay
            # WARNING: a bad JSON shape is an operator error worth surfacing,
            # not an expected quiescent state.)
            logger.debug(
                META_TOOLSMITH_ALLOWLIST_REQUIRED,
                note=(
                    "tool creation requested but no allowed_capabilities are "
                    "configured; holding tool creation off"
                ),
            )
        overrides["tool_creation_enabled"] = enabled
        toolsmith = _nested(overrides, "toolsmith")
        toolsmith["enabled"] = enabled
        if allowlist:
            toolsmith["allowed_capabilities"] = allowlist
    analysis = si.get("analysis_model", "").strip()
    if analysis:
        overrides["analysis_model"] = analysis
    code_model = si.get("code_modification_model", "").strip()
    if code_model:
        _nested(overrides, "code_modification")["llm_model"] = code_model

    cos_overrides = _nested(overrides, "chief_of_staff")
    for setting_key, field in _COS_BOOL_FIELDS.items():
        if setting_key in cos:
            cos_overrides[field] = _as_bool(cos[setting_key])
    for field in _COS_MODEL_FIELDS:
        value = cos.get(field, "").strip()
        if value:
            cos_overrides[field] = value

    charter = await _read_namespace(settings_service, SettingNamespace.CHARTER)
    charter_overrides = _nested(overrides, "charter")
    for field in _CHARTER_MODEL_FIELDS:
        value = charter.get(field, "").strip()
        if value:
            charter_overrides[field] = value
    for field in _CHARTER_SCALAR_FIELDS:
        if field in charter:
            charter_overrides[field] = charter[field]
    return overrides


async def _read_namespace(
    settings_service: SettingsServiceProtocol,
    namespace: SettingNamespace,
) -> dict[str, str]:
    """Read one settings namespace into a ``{key: value}`` map.

    Returns an empty map (and logs which namespace failed) on a
    non-critical read error, so the caller still applies the other
    namespace's overlay rather than dropping both.

    Returns:
        ``{key: value}`` for every entry in *namespace*, or ``{}`` on a
        non-critical read failure.
    """
    try:
        # Drop entries whose stored value is None: the overlay calls
        # ``.strip()`` on these, which an unset (None) value would crash on.
        return {
            entry.definition.key: entry.value
            for entry in await settings_service.get_namespace(namespace)
            if entry.value is not None
        }
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            META_SELF_IMPROVEMENT_LOAD_FAILED,
            reason="settings_namespace_read_error",
            namespace=namespace.value,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return {}
