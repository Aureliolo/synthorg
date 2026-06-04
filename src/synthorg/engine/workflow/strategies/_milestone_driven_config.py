"""Config validation for the milestone-driven ceremony strategy.

Holds the strategy-config key constants and the pure validators that
``MilestoneDrivenStrategy.validate_strategy_config`` delegates to. Each
validator raises ``ValueError`` / ``TypeError`` on a malformed config.
"""

from typing import TYPE_CHECKING, Any

from synthorg.observability import get_logger
from synthorg.observability.events.workflow import (
    SPRINT_STRATEGY_CONFIG_INVALID,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = get_logger(__name__)

_KEY_MILESTONES: str = "milestones"
_KEY_TRANSITION_MILESTONE: str = "transition_milestone"

_KNOWN_CONFIG_KEYS: frozenset[str] = frozenset(
    {_KEY_MILESTONES, _KEY_TRANSITION_MILESTONE},
)

_MAX_MILESTONES: int = 32
_MAX_NAME_LEN: int = 128


def validate_milestones(config: Mapping[str, Any]) -> None:
    """Validate the ``milestones`` config key.

    Raises:
        TypeError: When ``milestones`` is set but not a list.
        ValueError: When the list exceeds ``_MAX_MILESTONES``
            entries or any entry fails validation.
    """
    raw = config.get(_KEY_MILESTONES)
    if raw is None:
        return

    if not isinstance(raw, list):
        msg = "'milestones' must be a list"
        logger.warning(
            SPRINT_STRATEGY_CONFIG_INVALID,
            strategy="milestone_driven",
            key=_KEY_MILESTONES,
            value_type=type(raw).__name__,
        )
        raise TypeError(msg)

    if len(raw) > _MAX_MILESTONES:
        msg = f"'milestones' must have <= {_MAX_MILESTONES} entries, got {len(raw)}"
        logger.warning(
            SPRINT_STRATEGY_CONFIG_INVALID,
            strategy="milestone_driven",
            key=_KEY_MILESTONES,
            count=len(raw),
            limit=_MAX_MILESTONES,
        )
        raise ValueError(msg)

    seen_names: set[str] = set()
    for i, entry in enumerate(raw):
        _validate_single_milestone(entry, i, seen_names)


def validate_transition_milestone(config: Mapping[str, Any]) -> None:
    """Validate the ``transition_milestone`` config key.

    Raises:
        ValueError: When ``transition_milestone`` is set but is
            blank, not a string, or exceeds ``_MAX_NAME_LEN``.
    """
    raw = config.get(_KEY_TRANSITION_MILESTONE)
    if raw is None:
        return

    if isinstance(raw, bool) or not isinstance(raw, str) or not raw.strip():
        msg = "'transition_milestone' must be a non-empty string"
        logger.warning(
            SPRINT_STRATEGY_CONFIG_INVALID,
            strategy="milestone_driven",
            key=_KEY_TRANSITION_MILESTONE,
            value=raw,
        )
        raise ValueError(msg)

    if len(raw) > _MAX_NAME_LEN:
        msg = f"'transition_milestone' must be <= {_MAX_NAME_LEN} chars, got {len(raw)}"
        logger.warning(
            SPRINT_STRATEGY_CONFIG_INVALID,
            strategy="milestone_driven",
            key=_KEY_TRANSITION_MILESTONE,
            length=len(raw),
            limit=_MAX_NAME_LEN,
        )
        raise ValueError(msg)


def _validate_single_milestone(
    entry: object,
    index: int,
    seen_names: set[str],
) -> None:
    """Validate a single milestone entry in the config list.

    Raises:
        TypeError: When the entry is not a mapping.
        ValueError: When required string fields are missing / blank or
            the milestone ``name`` duplicates an earlier entry.
    """
    if not isinstance(entry, dict):
        msg = f"'milestones[{index}]' must be a mapping"
        logger.warning(
            SPRINT_STRATEGY_CONFIG_INVALID,
            strategy="milestone_driven",
            index=index,
        )
        raise TypeError(msg)

    _validate_milestone_string(entry, "name", index)
    _validate_milestone_string(entry, "ceremony", index)

    name = entry["name"].strip()
    if name in seen_names:
        msg = f"Duplicate milestone name: {name!r}"
        logger.warning(
            SPRINT_STRATEGY_CONFIG_INVALID,
            strategy="milestone_driven",
            duplicate=name,
        )
        raise ValueError(msg)
    seen_names.add(name)


def _validate_milestone_string(
    entry: dict[str, Any],
    key: str,
    index: int,
) -> None:
    """Validate that a milestone entry has a non-empty string key.

    Raises:
        ValueError: When ``entry[key]`` is missing, not a string, or
            exceeds ``_MAX_NAME_LEN`` characters.
    """
    value = entry.get(key)
    if isinstance(value, bool) or not isinstance(value, str) or not value.strip():
        msg = f"'milestones[{index}].{key}' must be a non-empty string"
        logger.warning(
            SPRINT_STRATEGY_CONFIG_INVALID,
            strategy="milestone_driven",
            key=f"milestones[{index}].{key}",
            value=value,
        )
        raise ValueError(msg)

    if len(value) > _MAX_NAME_LEN:
        msg = (
            f"'milestones[{index}].{key}' must be <= "
            f"{_MAX_NAME_LEN} chars, got {len(value)}"
        )
        logger.warning(
            SPRINT_STRATEGY_CONFIG_INVALID,
            strategy="milestone_driven",
            key=f"milestones[{index}].{key}",
            length=len(value),
            limit=_MAX_NAME_LEN,
        )
        raise ValueError(msg)
