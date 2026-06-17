# module-kind: code
"""Agent expansion for the template renderer.

Expands raw template agent dicts into ``AgentConfig``-compatible dicts:
auto-name generation, name deduplication, personality preset/inline
resolution, model-requirement resolution, and merge directive handling.
"""

from collections.abc import Mapping
from typing import Final

from pydantic import JsonValue, ValidationError

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.template import (
    TEMPLATE_RENDER_TYPE_ERROR,
    TEMPLATE_RENDER_VARIABLE_ERROR,
)
from synthorg.templates._preset_resolution import resolve_agent_personality
from synthorg.templates.errors import TemplateRenderError
from synthorg.templates.merge import DEFAULT_MERGE_DEPARTMENT
from synthorg.templates.model_requirements import (
    ModelRequirement,
    resolve_model_requirement,
)
from synthorg.templates.presets import generate_auto_name

# Placeholder provider name resolved by the engine at startup.
_DEFAULT_PROVIDER = "default"

# Routing-alias placeholder written into the agent ``model`` dict before the
# capability matcher pins a concrete id; the full requirement rides in
# ``model_requirement``. Overwritten by ``match_and_assign_models``.
_DEFAULT_MODEL_ALIAS: Final[str] = "medium"

# Default department when not specified in template agent config.
_DEFAULT_DEPARTMENT = DEFAULT_MERGE_DEPARTMENT

logger = get_logger(__name__)


def _expand_agents(
    raw_agents: list[dict[str, object]],
    *,
    has_extends: bool,
    locales: list[str] | None = None,
    custom_presets: Mapping[str, dict[str, JsonValue]] | None = None,
    preserve_merge_ids: bool = False,
) -> list[dict[str, object]]:
    """Expand template agent dicts into AgentConfig-compatible dicts.

    Args:
        raw_agents: List of agent dicts from rendered YAML.
        has_extends: Whether the template uses inheritance.
        locales: Faker locale codes for auto-name generation.
        custom_presets: Optional custom preset mapping.
        preserve_merge_ids: Preserve ``merge_id`` on expanded agents.

    Returns:
        List of dicts suitable for ``AgentConfig`` construction.
    """
    keep_merge = preserve_merge_ids or has_extends
    used_names: set[str] = set()
    expanded: list[dict[str, object]] = []
    for idx, agent in enumerate(raw_agents):
        expanded.append(
            _expand_single_agent(
                agent,
                idx,
                used_names,
                has_extends=has_extends,
                locales=locales,
                custom_presets=custom_presets,
                preserve_merge_id=keep_merge,
            ),
        )
    return expanded


def _expand_single_agent(  # noqa: PLR0913
    agent: dict[str, object],
    idx: int,
    used_names: set[str],
    *,
    has_extends: bool,
    locales: list[str] | None = None,
    custom_presets: Mapping[str, dict[str, JsonValue]] | None = None,
    preserve_merge_id: bool = False,
) -> dict[str, object]:
    """Expand a single template agent dict.

    Steps: auto-name generation, name deduplication, personality
    preset/inline resolution, model tier assignment, and merge
    directive handling.

    Args:
        agent: Raw agent dict from rendered YAML.
        idx: Zero-based index for error context.
        used_names: Set of already-used names for deduplication.
        has_extends: Whether the template uses inheritance.
        locales: Faker locale codes for auto-name generation.
        custom_presets: Optional custom preset mapping for resolving
            user-defined presets.
        preserve_merge_id: Preserve ``merge_id`` on the expanded agent.

    Returns:
        Expanded agent dict suitable for ``AgentConfig`` construction.

    Raises:
        TemplateRenderError: When ``role`` is absent, empty, or not a string.
    """
    role = agent.get("role")
    if not isinstance(role, str) or not role.strip():
        msg = f"Agent at index {idx} requires a non-empty string 'role' field"
        logger.warning(TEMPLATE_RENDER_VARIABLE_ERROR, index=idx, field="role")
        raise TemplateRenderError(msg)
    role = role.strip()
    name = str(agent.get("name") or "").strip()

    if not name or name.startswith("{{") or "__JINJA2__" in name:
        name = generate_auto_name(role, seed=idx, locales=locales)

    base_name = name
    counter = 2
    while name in used_names:
        name = f"{base_name} {counter}"
        counter += 1
    used_names.add(name)

    agent_dict: dict[str, object] = {
        "name": name,
        "role": role,
        "department": agent.get("department", _DEFAULT_DEPARTMENT),
        "level": agent.get("level", "mid"),
    }

    personality = resolve_agent_personality(
        agent,
        name,
        custom_presets=custom_presets,
    )
    if personality is not None:
        agent_dict["personality"] = personality

    preset = _agent_preset_name(agent)
    if preset is not None:
        agent_dict["personality_preset"] = preset

    requirement = _resolve_model_requirement(agent, preset)
    agent_dict["model_requirement"] = requirement.model_dump()
    placeholder = requirement.model_id or _DEFAULT_MODEL_ALIAS
    agent_dict["model"] = {"provider": _DEFAULT_PROVIDER, "model_id": placeholder}

    # Preserve _remove merge directive for inheritance.
    if agent.get("_remove"):
        if not has_extends:
            msg = (
                f"Agent {name!r} uses '_remove' but the template "
                "has no 'extends' -- directive has no effect"
            )
            logger.warning(
                TEMPLATE_RENDER_VARIABLE_ERROR,
                agent=name,
                field="_remove",
            )
            raise TemplateRenderError(msg)
        agent_dict["_remove"] = True

    # Preserve merge_id when inheritance is active or when rendering
    # as a parent (so child templates can target agents by merge_id).
    keep_merge = preserve_merge_id or has_extends
    merge_id_raw = agent.get("merge_id") or ""
    merge_id = str(merge_id_raw).strip()
    if keep_merge and merge_id:
        agent_dict["merge_id"] = merge_id

    return agent_dict


def _agent_preset_name(agent: dict[str, object]) -> str | None:
    """Return the named personality preset, when the agent uses one.

    A template agent references a preset by a bare ``personality`` string;
    an inline ``personality`` dict has no preset name.

    Returns:
        The preset name, or ``None`` for inline / absent personality.
    """
    raw = agent.get("personality")
    return raw.strip() if isinstance(raw, str) and raw.strip() else None


def _resolve_model_requirement(
    agent: dict[str, object],
    preset: str | None,
) -> ModelRequirement:
    """Resolve an agent's model reference into a full ``ModelRequirement``.

    A bare ``model`` string pins an explicit example id; a dict maps onto
    the capability/family fields. Personality-preset affinity supplies
    capability defaults that the explicit reference overrides. The full
    requirement is preserved on the expanded agent so the capability
    matcher can pin a concrete id (no lossy tier collapse).

    Args:
        agent: Raw template agent dict from Jinja2 rendering.
        preset: Resolved personality preset name (or ``None``).

    Returns:
        The resolved ``ModelRequirement``.

    Raises:
        TemplateRenderError: If a dict model reference has invalid fields.
    """
    model_raw = agent.get("model")
    overrides: dict[str, JsonValue]
    if isinstance(model_raw, dict):
        overrides = model_raw
    elif isinstance(model_raw, str) and model_raw.strip():
        overrides = {"model_id": model_raw.strip()}
    else:
        overrides = {}

    try:
        return resolve_model_requirement(preset, overrides)
    except (ValidationError, ValueError) as exc:
        msg = f"Invalid model reference: {safe_error_description(exc)}"
        logger.warning(
            TEMPLATE_RENDER_TYPE_ERROR,
            field="model",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise TemplateRenderError(msg) from exc
