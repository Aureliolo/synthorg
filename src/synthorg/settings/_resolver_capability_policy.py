# module-kind: code
"""Composed getter for the capability policy's configuration.

A free function rather than a ``ConfigResolver`` method: every composed
getter there merges onto the YAML base config the resolver holds, and this
one deliberately does not (the ladder is built from settings alone, so half
of a comparison could never come from a file and the other half from the
database). It needs only the string accessor, so that is all it asks for.

Every key is spelled out. A key built by interpolation is a key the
liveness gate cannot see, and the ladder is precisely the surface where a
setting that binds nothing would be invisible: the write persists, the
dashboard renders it, and the next assignment reads the old floor.
"""

import asyncio
from typing import Protocol, runtime_checkable

from synthorg.core.task_enums import Stakes
from synthorg.engine.routing_policy.config import (
    CapabilityPolicyConfig,
    StakesCapabilityFloor,
    StakesReasoning,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import SETTINGS_FETCH_FAILED
from synthorg.settings._resolver_coercions import _parse_reasoning_effort

logger = get_logger(__name__)

_NAMESPACE = "engine"


@runtime_checkable
class StringSettingReader(Protocol):
    """The one accessor this getter needs from the resolver.

    Annotating against the leaf rather than ``ConfigResolver`` keeps the
    dependency one-way: the resolver imports this module, not the reverse.
    """

    async def get_str(self, namespace: str, key: str) -> str:
        """Return the resolved string value of one setting."""
        ...


async def resolve_capability_policy_config(
    reader: StringSettingReader,
) -> CapabilityPolicyConfig:
    """Assemble the capability policy's configuration from settings.

    Every field is registered, so the config is built from scratch rather
    than merged onto the YAML base: a partially-settings-backed ladder would
    let one half of a comparison come from a file and the other from the
    database.

    Args:
        reader: The settings resolver, read through its string accessor.

    Returns:
        A ``CapabilityPolicyConfig`` reflecting the live settings.

    Raises:
        SettingNotFoundError: If a capability-policy setting is missing from
            the registry.
        ValueError: If a resolved value cannot be parsed, or if the resolved
            floors or reasoning efforts invert the stakes ladder.
    """
    try:
        async with asyncio.TaskGroup() as tg:
            floor_low = tg.create_task(
                reader.get_str(_NAMESPACE, "capability_floor_low")
            )
            floor_normal = tg.create_task(
                reader.get_str(_NAMESPACE, "capability_floor_normal")
            )
            floor_high = tg.create_task(
                reader.get_str(_NAMESPACE, "capability_floor_high")
            )
            floor_critical = tg.create_task(
                reader.get_str(_NAMESPACE, "capability_floor_critical")
            )
            effort_low = tg.create_task(
                reader.get_str(_NAMESPACE, "reasoning_effort_low")
            )
            effort_normal = tg.create_task(
                reader.get_str(_NAMESPACE, "reasoning_effort_normal")
            )
            effort_high = tg.create_task(
                reader.get_str(_NAMESPACE, "reasoning_effort_high")
            )
            effort_critical = tg.create_task(
                reader.get_str(_NAMESPACE, "reasoning_effort_critical")
            )
            red_team = tg.create_task(reader.get_str(_NAMESPACE, "red_team_min_stakes"))
            park = tg.create_task(
                reader.get_str(_NAMESPACE, "capability_park_min_stakes")
            )
    except ExceptionGroup as eg:
        first_failure = eg.exceptions[0]
        logger.warning(
            SETTINGS_FETCH_FAILED,
            namespace=_NAMESPACE,
            key="_capability_policy",
            error_count=len(eg.exceptions),
            error_type=type(first_failure).__name__,
            error=safe_error_description(first_failure),
        )
        raise first_failure from eg

    return CapabilityPolicyConfig(
        capability_floors=StakesCapabilityFloor.model_validate(
            {
                "low": floor_low.result(),
                "normal": floor_normal.result(),
                "high": floor_high.result(),
                "critical": floor_critical.result(),
            }
        ),
        reasoning=StakesReasoning.model_validate(
            {
                "low": _parse_reasoning_effort(effort_low.result()),
                "normal": _parse_reasoning_effort(effort_normal.result()),
                "high": _parse_reasoning_effort(effort_high.result()),
                "critical": _parse_reasoning_effort(effort_critical.result()),
            }
        ),
        red_team_min_stakes=Stakes(red_team.result()),
        park_min_stakes=Stakes(park.result()),
    )


__all__ = ["StringSettingReader", "resolve_capability_policy_config"]
