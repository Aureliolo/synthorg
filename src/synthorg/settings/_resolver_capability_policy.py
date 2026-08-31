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

from synthorg.core.completion_enums import ReasoningEffort, reasoning_effort_rank
from synthorg.core.task_enums import Stakes
from synthorg.engine.routing_policy.config import (
    CapabilityPolicyConfig,
    StakesCapabilityFloor,
    StakesReasoning,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_FETCH_FAILED,
    SETTINGS_REASONING_LADDER_REPAIRED,
)
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

    reasoning = _repair_inverted_reasoning_ladder(
        low=_parse_reasoning_effort(effort_low.result()),
        normal=_parse_reasoning_effort(effort_normal.result()),
        high=_parse_reasoning_effort(effort_high.result()),
        critical=_parse_reasoning_effort(effort_critical.result()),
    )
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
                "low": reasoning[0],
                "normal": reasoning[1],
                "high": reasoning[2],
                "critical": reasoning[3],
            }
        ),
        red_team_min_stakes=Stakes(red_team.result()),
        park_min_stakes=Stakes(park.result()),
    )


def _rank(effort: ReasoningEffort | None) -> int:
    """Rank a parsed reasoning effort, with unset ranking below every tier.

    Returns:
        The effort's rank, or ``-1`` for unset.
    """
    return -1 if effort is None else reasoning_effort_rank(effort)


def _repair_inverted_reasoning_ladder(
    *,
    low: ReasoningEffort | None,
    normal: ReasoningEffort | None,
    high: ReasoningEffort | None,
    critical: ReasoningEffort | None,
) -> tuple[
    ReasoningEffort | None,
    ReasoningEffort | None,
    ReasoningEffort | None,
    ReasoningEffort | None,
]:
    """Cap each tier to the one above it so the resolved ladder never inverts.

    ``enforce_engine_ladders`` refuses an inverted ladder at WRITE time, so
    an inversion can only reach here when a REGISTERED DEFAULT moves past a
    value an operator explicitly wrote for a higher-stakes tier before the
    change (raising ``engine.reasoning_effort_low``'s default past an
    existing, lower, explicit ``engine.reasoning_effort_normal`` is exactly
    this). ``StakesReasoning``'s own validator would otherwise raise here,
    which ``resolve_capability_policy_config`` cannot recover from: it is
    called from boot wiring with nothing upstream to catch it.

    Capping only ever LOWERS the lower-stakes side of an inverted pair, so
    the repair never asks a model for reasoning depth it did not already
    request (no risk of an unsupported-value rejection) and never overrides
    an operator's higher-stakes choice. Logged once per call when a cap
    actually fires; the operator's own write (raising the lower tier or
    lowering the higher one) is what clears it on a later read.

    Returns:
        The four efforts, guaranteed non-decreasing in rank.
    """
    tiers: list[ReasoningEffort | None] = [low, normal, high, critical]
    repaired = False
    for i in range(len(tiers) - 2, -1, -1):
        if _rank(tiers[i]) > _rank(tiers[i + 1]):
            tiers[i] = tiers[i + 1]
            repaired = True
    if repaired:
        logger.warning(
            SETTINGS_REASONING_LADDER_REPAIRED,
            resolved_low=str(low) if low else "none",
            resolved_normal=str(normal) if normal else "none",
            resolved_high=str(high) if high else "none",
            resolved_critical=str(critical) if critical else "none",
            repaired_low=str(tiers[0]) if tiers[0] else "none",
            repaired_normal=str(tiers[1]) if tiers[1] else "none",
            repaired_high=str(tiers[2]) if tiers[2] else "none",
            repaired_critical=str(tiers[3]) if tiers[3] else "none",
        )
    return (tiers[0], tiers[1], tiers[2], tiers[3])


__all__ = ["StringSettingReader", "resolve_capability_policy_config"]
