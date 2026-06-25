# module-kind: code
"""Overlay individual feature settings onto the self-improvement config.

The self-improvement and Chief-of-Staff feature flags and per-feature
models are individual runtime settings (namespaces ``self_improvement``
and ``chief_of_staff``), so the wizard and dashboard Settings can toggle
them over the standard ``/settings`` API. They are the single source of
truth: this overlay applies them onto the dict parsed from the
``meta.self_improvement`` structural blob, always winning over any legacy
flag value the blob may still carry. Only the deep structural tuning
(schedule, rollout, regression, guards, toolsmith internals) is sourced
from the blob.
"""

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meta import META_SELF_IMPROVEMENT_LOAD_FAILED
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
# capability is surfaced as "explain_chat" in settings/UI but the config
# field is the historical ``chat_enabled``.
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

_COS_MODEL_FIELDS: dict[str, str] = {
    "chat_model": "chat_model",
    "propose_model": "propose_model",
    "routing_model": "routing_model",
    "narrative_model": "narrative_model",
}


def _as_bool(value: str) -> bool:
    """Coerce a stored setting string to a boolean.

    Returns:
        ``True`` when *value* is a recognised true token.
    """
    return value.strip().lower() in _TRUE_TOKENS


def _nested(overrides: dict[str, object], key: str) -> dict[str, object]:
    """Return the nested sub-config dict for *key*, creating it if absent.

    Returns:
        A mutable dict stored under ``overrides[key]``.
    """
    nested = overrides.get(key)
    if not isinstance(nested, dict):
        nested = {}
        overrides[key] = nested
    return nested


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
    try:
        si = {
            entry.definition.key: entry.value
            for entry in await settings_service.get_namespace(
                SettingNamespace.SELF_IMPROVEMENT
            )
        }
        cos = {
            entry.definition.key: entry.value
            for entry in await settings_service.get_namespace(
                SettingNamespace.CHIEF_OF_STAFF
            )
        }
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            META_SELF_IMPROVEMENT_LOAD_FAILED,
            reason="settings_namespace_read_error",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return overrides

    for key in _SI_BOOL_FIELDS:
        if key in si:
            overrides[key] = _as_bool(si[key])
    # tool_creation_enabled must agree with toolsmith.enabled (the
    # SelfImprovementConfig cross-field validator enforces this).
    if "tool_creation_enabled" in overrides:
        _nested(overrides, "toolsmith")["enabled"] = overrides["tool_creation_enabled"]
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
    for setting_key, field in _COS_MODEL_FIELDS.items():
        value = cos.get(setting_key, "").strip()
        if value:
            cos_overrides[field] = value
    return overrides
