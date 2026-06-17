"""Agent-related helpers for the first-run setup controller.

Handles template agent expansion, model matching, and persistence
operations that were previously inline in ``setup.py``.
"""

import json
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, cast

from pydantic import JsonValue

from synthorg.api.controllers.setup_models import SetupAgentRequest, SetupAgentSummary
from synthorg.config.schema import ProviderConfig
from synthorg.core.domain_errors import ValidationError
from synthorg.core.normalization import normalize_optional_string
from synthorg.observability import get_logger
from synthorg.observability.events.setup import (
    SETUP_AGENT_SUMMARY_MISSING_FIELDS,
    SETUP_AGENTS_CORRUPTED,
    SETUP_AGENTS_READ_FALLBACK,
    SETUP_MODEL_FALLBACK_USED,
    SETUP_PRESET_NOT_FOUND,
    SETUP_TEMPLATE_INVALID,
)
from synthorg.settings.enums import SettingSource
from synthorg.settings.errors import SettingNotFoundError
from synthorg.settings.service import SettingsService
from synthorg.templates.model_matcher_config import ModelMatcherConfig
from synthorg.templates.schema import CompanyTemplate, TemplateDepartmentConfig

if TYPE_CHECKING:
    # Referenced only inside a string-literal ``cast`` annotation, so the name
    # never resolves at runtime: keep it guarded to avoid importing a private
    # type across the package boundary.
    from synthorg.templates.model_matcher import _ProviderWithModels

logger = get_logger(__name__)

# Required keys every agent dict must have in the persisted list.
_REQUIRED_AGENT_KEYS: frozenset[str] = frozenset({"name", "role"})


def expand_template_agents(
    template: CompanyTemplate,
    locales: list[str] | None = None,
    *,
    custom_presets: Mapping[str, dict[str, JsonValue]] | None = None,
) -> list[dict[str, object]]:
    """Expand template agent configs into persistable agent dicts.

    Uses the same building blocks as the renderer (personality presets,
    auto-name generation) but does not require a full ``RootConfig``
    validation pass.

    Args:
        template: Parsed ``CompanyTemplate`` from the loader.
        locales: Faker locale codes for name generation.  ``None``
            uses all Latin-script locales.
        custom_presets: Optional mapping of custom preset names to
            personality config dicts (checked before builtins).

    Returns:
        List of agent config dicts with ``tier`` metadata and, when
        the template uses structured model requirements, a
        ``model_requirement`` dict for downstream matching.

    Raises:
        ValidationError: If a structured model requirement dict
            contains invalid fields.
    """
    from synthorg.templates.presets import (  # noqa: PLC0415
        generate_auto_name,
        get_personality_preset,
    )

    agents: list[dict[str, object]] = []
    used_names: set[str] = set()

    for idx, agent_cfg in enumerate(template.agents):
        name = agent_cfg.name.strip() if agent_cfg.name else ""
        if not name or name.startswith("{{") or "__JINJA2__" in name:
            name = generate_auto_name(agent_cfg.role, seed=idx, locales=locales)

        # Deduplicate names.
        base_name = name
        counter = 2
        while name in used_names:
            name = f"{base_name} {counter}"
            counter += 1
        used_names.add(name)

        # Resolve personality.
        preset_name = agent_cfg.personality_preset or "pragmatic_builder"
        try:
            personality = get_personality_preset(
                preset_name,
                custom_presets=custom_presets,
            )
        except KeyError:
            logger.warning(
                SETUP_TEMPLATE_INVALID,
                preset=preset_name,
                agent_index=idx,
                reason="unknown_personality_preset",
            )
            preset_name = "pragmatic_builder"
            personality = get_personality_preset(preset_name)

        # Resolve model tier and optional structured ModelRequirement.
        tier: str
        if isinstance(agent_cfg.model, dict):
            from synthorg.templates.model_requirements import (  # noqa: PLC0415
                parse_model_requirement,
            )

            model_req = parse_model_requirement(agent_cfg.model)
            tier = model_req.tier
        else:
            model_req = None
            tier = agent_cfg.model

        agent_dict: dict[str, object] = {
            "name": name,
            "role": agent_cfg.role,
            "department": agent_cfg.department or "engineering",
            "level": agent_cfg.level.value,
            "personality": personality,
            "personality_preset": preset_name,
            "tier": tier,
            "model": {"provider": "", "model_id": ""},
        }
        if model_req is not None:
            agent_dict["model_requirement"] = model_req.model_dump()
        agents.append(agent_dict)

    return agents


def match_and_assign_models(
    agents: list[dict[str, object]],
    providers: Mapping[str, ProviderConfig],
    matcher_config: ModelMatcherConfig | None = None,
) -> list[dict[str, object]]:
    """Auto-assign models to template agents using the matching engine.

    Returns a new list of agent dicts with ``model.provider`` and
    ``model.model_id`` set to the best available match.  The input
    list is not modified.

    Args:
        agents: Expanded agent config dicts from ``expand_template_agents``.
        providers: Provider name -> config mapping.
        matcher_config: Optional :class:`ModelMatcherConfig` carrying
            operator-tunable score weights resolved from
            ``EngineBridgeConfig``. ``None`` falls back to the matcher
            defaults that mirror the historical hardcoded values.

    Returns:
        New list of agent dicts with model assignments applied.
    """
    from synthorg.templates.model_matcher import match_all_agents  # noqa: PLC0415

    # ProviderConfig structurally exposes ``models`` but its frozen field
    # is not assignable to the matcher protocol's mutable attribute; the
    # cast bridges the read-only/mutable gap at this read-only call.
    matches = match_all_agents(
        agents,
        cast("Mapping[str, _ProviderWithModels]", providers),
        matcher_config,
    )
    match_map = {
        m.agent_index: {
            "provider": m.provider_name,
            "model_id": m.model_id,
            "model_tier": m.tier,
        }
        for m in matches
    }
    result: list[dict[str, object]] = []
    for idx, agent in enumerate(agents):
        if idx in match_map:
            result.append({**agent, "model": match_map[idx]})
        else:
            # Log at DEBUG. The matcher's tier-fallback path is the
            # documented contract; only the wizard's pre-flight provider
            # gate escalates "no models at all" to a 422 user error.
            # This branch fires when the matcher returned no match for
            # an agent (rare with the gate in place); leaving a DEBUG
            # breadcrumb still lets operators trace it without polluting
            # WARNING-level logs.
            logger.debug(
                SETUP_MODEL_FALLBACK_USED,
                agent_index=idx,
                agent_name=agent.get("name", ""),
                tier=agent.get("tier", ""),
                reason="no_match_returned",
            )
            result.append(dict(agent))
    return result


def build_agent_config(
    data: SetupAgentRequest,
    *,
    custom_presets: Mapping[str, dict[str, JsonValue]] | None = None,
) -> dict[str, object]:
    """Build an agent config dict for settings persistence.

    Args:
        data: Validated agent creation payload.
        custom_presets: Optional custom preset mapping.

    Returns:
        Agent configuration dict suitable for JSON serialization.

    Raises:
        ValidationError: If the personality preset name is not
            found in either custom or builtin presets.
    """
    from synthorg.templates.presets import get_personality_preset  # noqa: PLC0415

    try:
        personality_dict = get_personality_preset(
            data.personality_preset,
            custom_presets=custom_presets,
        )
    except KeyError:
        logger.warning(
            SETUP_PRESET_NOT_FOUND,
            preset=data.personality_preset,
        )
        msg = f"Unknown personality preset {data.personality_preset!r}"
        raise ValidationError(msg) from None
    agent_config: dict[str, object] = {
        "name": data.name,
        "role": data.role,
        "department": data.department,
        "level": data.level.value,
        "personality": personality_dict,
        "personality_preset": data.personality_preset,
        "model": {
            "provider": data.model_provider,
            "model_id": data.model_id,
        },
    }
    if data.budget_limit_monthly is not None:
        agent_config["budget_limit_monthly"] = data.budget_limit_monthly
    return agent_config


async def get_existing_agents(
    settings_svc: SettingsService,
) -> list[dict[str, object]]:
    """Read the current agents list from settings.

    Only the "entry not found" case yields an empty list. JSON parse
    errors and non-list values are surfaced so callers do not silently
    overwrite corrupted data.

    Args:
        settings_svc: Settings service instance.

    Returns:
        List of agent config dicts (empty if entry is absent or None).

    Raises:
        ValidationError: If the stored value is not valid JSON or
            not a JSON array.
        MemoryError: Raised on the corresponding failure path.
        RecursionError: Raised on the corresponding failure path.
    """
    try:
        entry = await settings_svc.get_entry("company", "agents")
    except MemoryError, RecursionError:
        raise
    except SettingNotFoundError:
        logger.debug(SETUP_AGENTS_READ_FALLBACK, reason="entry_not_found")
        return []

    if entry.source != SettingSource.DATABASE:
        logger.debug(
            SETUP_AGENTS_READ_FALLBACK,
            reason="non_database_source",
            source=entry.source,
        )
        return []

    try:
        parsed = json.loads(entry.value)
    except json.JSONDecodeError as exc:
        logger.warning(
            SETUP_AGENTS_CORRUPTED,
            reason="invalid_json",
        )
        msg = "Stored agents list is not valid JSON"
        raise ValidationError(msg) from exc

    if not isinstance(parsed, list):
        logger.warning(
            SETUP_AGENTS_CORRUPTED,
            reason="non_list_json",
            raw_type=type(parsed).__name__,
        )
        msg = f"Stored agents list is {type(parsed).__name__}, expected list"
        raise ValidationError(msg)

    _validate_agent_elements(parsed)
    return parsed


def _validate_agent_elements(parsed: list[object]) -> None:
    """Validate each element in a parsed agents list.

    Raises:
        ValidationError: If any element is not a dict with valid
            string values for required keys.
    """
    for idx, element in enumerate(parsed):
        if not isinstance(element, dict):
            logger.warning(
                SETUP_AGENTS_CORRUPTED,
                reason="non_dict_element",
                element_index=idx,
                element_type=type(element).__name__,
            )
            msg = f"Agent at index {idx} is {type(element).__name__}, expected dict"
            raise ValidationError(msg)
        if not _REQUIRED_AGENT_KEYS.issubset(element.keys()):
            logger.warning(
                SETUP_AGENTS_CORRUPTED,
                reason="missing_keys",
                element_index=idx,
                present_keys=sorted(element.keys()),
            )
            msg = f"Agent at index {idx} missing required keys (need 'name' and 'role')"
            raise ValidationError(msg)
        for key in _REQUIRED_AGENT_KEYS:
            val = element[key]
            if not isinstance(val, str) or not val.strip():
                logger.warning(
                    SETUP_AGENTS_CORRUPTED,
                    reason="invalid_field_value",
                    element_index=idx,
                    field=key,
                    value_type=type(val).__name__,
                )
                msg = f"Agent at index {idx}: '{key}' must be a non-empty string"
                raise ValidationError(msg)


def validate_agents_value(raw: str, *, strict: bool) -> bool:
    """Parse *raw* as JSON and return True if it is a non-empty list.

    When *strict* is True, raises ``ValidationError`` on corrupted
    data instead of returning False.

    Args:
        raw: Raw JSON string from settings.
        strict: When True, raise on corrupted data.

    Returns:
        True if the value is a non-empty JSON list.

    Raises:
        ValidationError: Raised on the corresponding failure path.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning(
            SETUP_AGENTS_CORRUPTED,
            reason="invalid_json",
        )
        if strict:
            msg = "Stored agents list is not valid JSON"
            raise ValidationError(msg) from exc
        return False

    if not isinstance(parsed, list):
        logger.warning(
            SETUP_AGENTS_CORRUPTED,
            reason="non_list_json",
            raw_type=type(parsed).__name__,
        )
        if strict:
            msg = f"Stored agents list is {type(parsed).__name__}, expected list"
            raise ValidationError(msg)
        return False

    return bool(parsed)


def normalize_description(raw: str | None) -> str | None:
    """Strip whitespace from description, treating blank as None.

    Returns:
        The ``str`` value when present, ``None`` otherwise.
    """
    return normalize_optional_string(raw)


def departments_to_json(
    departments: Sequence[TemplateDepartmentConfig],
) -> str:
    """Convert template departments to a JSON string.

    Returns:
        Resulting string.
    """
    if not departments:
        return ""
    dept_list = [
        {"name": d.name, "budget_percent": d.budget_percent} for d in departments
    ]
    return json.dumps(dept_list)


def agents_to_summaries(
    agents: list[dict[str, object]],
) -> tuple[SetupAgentSummary, ...]:
    """Convert agent config dicts to summary DTOs.

    Returns:
        Tuple of the declared element types.
    """
    return tuple(agent_dict_to_summary(a) for a in agents)


def _agent_str(agent: Mapping[str, object], key: str) -> str:
    """Return the stripped string at *key*, or empty when not a string.

    Returns:
        The stripped value, or an empty string.
    """
    value = agent.get(key)
    return value.strip() if isinstance(value, str) else ""


def _agent_opt_str(value: object) -> str | None:
    """Normalise an optional string field, treating non-strings as absent.

    Returns:
        The normalised string, or ``None``.
    """
    return normalize_optional_string(value) if isinstance(value, str) else None


def agent_dict_to_summary(
    agent: dict[str, object],
) -> SetupAgentSummary:
    """Convert a single agent config dict to a summary DTO.

    Returns:
        ``SetupAgentSummary`` instance.
    """
    # Normalize string fields so whitespace-only values fall through
    # to defaults (NotBlankStr rejects blank strings).
    name = _agent_str(agent, "name") or "unknown"
    role = _agent_str(agent, "role") or "unknown"
    department = _agent_str(agent, "department") or "general"
    missing = [
        f
        for f, v in (("name", name), ("role", role), ("department", department))
        if v in ("unknown", "general") and not _agent_str(agent, f)
    ]
    if missing:
        logger.warning(
            SETUP_AGENT_SUMMARY_MISSING_FIELDS,
            missing_fields=missing,
            agent_keys=list(agent.keys()),
        )
    model = agent.get("model")
    model_dict = model if isinstance(model, dict) else {}
    # model_validate coerces the persisted string ``level`` / ``tier``
    # values against SetupAgentSummary's enum / Literal fields.
    return SetupAgentSummary.model_validate(
        {
            "name": name,
            "role": role,
            "department": department,
            "level": _agent_opt_str(agent.get("level")),
            "model_provider": _agent_opt_str(model_dict.get("provider")),
            "model_id": _agent_opt_str(model_dict.get("model_id")),
            "tier": _agent_str(agent, "tier") or "medium",
            "personality_preset": _agent_opt_str(agent.get("personality_preset")),
        },
    )
