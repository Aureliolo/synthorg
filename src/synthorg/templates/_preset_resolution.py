"""Personality preset resolution helpers for the renderer.

Internal module -- should not be imported outside the ``templates``
package.
"""

import copy
from typing import TYPE_CHECKING

from pydantic import JsonValue, ValidationError

from synthorg.core.agent import PersonalityConfig
from synthorg.core.normalization import normalize_ascii_lowercase
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.template import (
    TEMPLATE_PRESET_RESOLVED_CUSTOM,
    TEMPLATE_RENDER_TYPE_ERROR,
    TEMPLATE_RENDER_VALIDATION_ERROR,
)
from synthorg.templates.errors import TemplateRenderError
from synthorg.templates.presets import get_personality_preset

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = get_logger(__name__)


def resolve_agent_personality(
    agent: dict[str, object],
    name: str,
    *,
    custom_presets: Mapping[str, dict[str, JsonValue]] | None = None,
) -> dict[str, JsonValue] | None:
    """Resolve personality from inline config or named preset.

    Inline personality config takes highest precedence.  For named
    presets, custom presets are checked first, then builtins.  When a
    named preset is not found in either source, a warning is logged
    and ``None`` is returned (the agent proceeds without a
    personality).

    Args:
        agent: Raw agent dict from rendered YAML.
        name: Resolved agent name for error context.
        custom_presets: Optional custom preset mapping.

    Returns:
        Personality dict, or ``None`` if no personality configured or
        the referenced preset does not exist.

    Raises:
        TemplateRenderError: If an inline personality config is invalid.
    """
    inline_personality = agent.get("personality")
    preset_name = agent.get("personality_preset")
    if inline_personality is not None:
        if not isinstance(inline_personality, dict):
            msg = (
                f"Personality for agent {name!r} must be a mapping, "
                f"got {type(inline_personality).__name__}"
            )
            logger.warning(
                TEMPLATE_RENDER_TYPE_ERROR,
                agent=name,
                field="personality",
                got=type(inline_personality).__name__,
            )
            raise TemplateRenderError(msg)
        _validate_inline_personality(inline_personality, name)
        return copy.deepcopy(inline_personality)
    if preset_name:
        if not isinstance(preset_name, str):
            msg = (
                f"personality_preset for agent {name!r} must be a string, "
                f"got {type(preset_name).__name__}"
            )
            logger.warning(
                TEMPLATE_RENDER_TYPE_ERROR,
                agent=name,
                field="personality_preset",
                got=type(preset_name).__name__,
            )
            raise TemplateRenderError(msg)
        # Normalize once for both the lookup and the custom-source check.
        key = normalize_ascii_lowercase(preset_name)
        is_custom = custom_presets is not None and key in custom_presets
        try:
            result = get_personality_preset(
                preset_name,
                custom_presets=custom_presets,
            )
        except KeyError:
            # Warning already logged by get_personality_preset.
            return None
        if is_custom:
            logger.debug(
                TEMPLATE_PRESET_RESOLVED_CUSTOM,
                agent=name,
                preset=preset_name,
            )
        return result
    return None


def _validate_inline_personality(
    personality: dict[str, JsonValue],
    agent_name: str,
) -> None:
    """Eagerly validate an inline personality dict.

    Args:
        personality: Raw personality dict from template YAML.
        agent_name: Agent name for error context.

    Raises:
        TemplateRenderError: If the dict is not valid for PersonalityConfig.
    """
    try:
        PersonalityConfig.model_validate(personality)
    except (ValidationError, TypeError) as exc:
        desc = safe_error_description(exc)
        logger.warning(
            TEMPLATE_RENDER_VALIDATION_ERROR,
            agent_name=agent_name,
            error_type=type(exc).__name__,
            error=desc,
        )
        msg = f"Invalid inline personality for agent {agent_name!r}: {desc}"
        raise TemplateRenderError(msg) from exc
