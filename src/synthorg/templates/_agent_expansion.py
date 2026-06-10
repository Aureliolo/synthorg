# module-kind: code
"""Agent expansion for the template renderer.

Expands raw template agent dicts into ``AgentConfig``-compatible dicts:
auto-name generation, name deduplication, personality preset/inline
resolution, model tier assignment, and merge directive handling.
"""

from collections.abc import Mapping

from pydantic import JsonValue, ValidationError

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.template import (
    TEMPLATE_RENDER_TYPE_ERROR,
    TEMPLATE_RENDER_VARIABLE_ERROR,
)
from synthorg.templates._preset_resolution import resolve_agent_personality
from synthorg.templates.errors import TemplateRenderError
from synthorg.templates.merge import DEFAULT_MERGE_DEPARTMENT
from synthorg.templates.presets import generate_auto_name

# Placeholder provider name resolved by the engine at startup.
_DEFAULT_PROVIDER = "default"

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

    model_tier = _resolve_model_tier(agent)
    agent_dict["model"] = {"provider": _DEFAULT_PROVIDER, "model_id": model_tier}

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


def _resolve_model_tier(agent: dict[str, object]) -> str:
    """Extract the model tier from a template agent dict.

    Handles both the string format (``"medium"``) and the structured
    ``ModelRequirement`` dict format
    (``{"tier": "medium", "priority": "quality"}``).

    The renderer path sets a placeholder ``model_id``; structured
    requirements are only fully threaded through the setup wizard path
    which calls ``match_all_agents``.

    Args:
        agent: Raw template agent dict from Jinja2 rendering.

    Returns:
        Tier string (``"large"``, ``"medium"``, or ``"small"``).

    Raises:
        TemplateRenderError: If a dict model contains invalid fields.
    """
    model_raw = agent.get("model", "medium")
    if isinstance(model_raw, dict):
        from synthorg.templates.model_requirements import (  # noqa: PLC0415
            parse_model_requirement,
        )

        try:
            return parse_model_requirement(model_raw).tier
        except (ValidationError, ValueError) as exc:
            msg = f"Invalid structured model requirement: {safe_error_description(exc)}"
            logger.warning(
                TEMPLATE_RENDER_TYPE_ERROR,
                field="model",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise TemplateRenderError(msg) from exc
    return str(model_raw)
