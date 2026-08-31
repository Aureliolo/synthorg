"""Tests for the capability-policy resolver's reasoning-ladder self-heal.

``enforce_engine_ladders`` refuses an inverted reasoning-effort ladder at
WRITE time. It cannot stop a REGISTERED DEFAULT moving past a value an
operator explicitly wrote for a higher-stakes tier before the default
changed, and ``StakesReasoning``'s own validator has no caller upstream to
catch it: it is built from boot wiring with no recovery path. The resolver
repairs the resolved values before constructing the model instead.
"""

import pytest
import structlog.testing

from synthorg.core.completion_enums import ReasoningEffort
from synthorg.observability.events.settings import SETTINGS_REASONING_LADDER_REPAIRED
from synthorg.settings._resolver_capability_policy import (
    resolve_capability_policy_config,
)

pytestmark = pytest.mark.unit

_DEFAULTS = {
    ("engine", "capability_floor_low"): "basic",
    ("engine", "capability_floor_normal"): "capable",
    ("engine", "capability_floor_high"): "expert",
    ("engine", "capability_floor_critical"): "expert",
    ("engine", "reasoning_effort_low"): "low",
    ("engine", "reasoning_effort_normal"): "low",
    ("engine", "reasoning_effort_high"): "medium",
    ("engine", "reasoning_effort_critical"): "high",
    ("engine", "red_team_min_stakes"): "high",
    ("engine", "capability_park_min_stakes"): "critical",
}


class _FakeReader:
    def __init__(self, overrides: dict[str, str]) -> None:
        self._values = {**{k: v for (_, k), v in _DEFAULTS.items()}, **overrides}

    async def get_str(self, namespace: str, key: str) -> str:
        return self._values[key]


async def test_a_non_inverted_ladder_passes_through_unchanged() -> None:
    reader = _FakeReader({})
    with structlog.testing.capture_logs() as logs:
        config = await resolve_capability_policy_config(reader)
    assert config.reasoning.low == ReasoningEffort.LOW
    assert config.reasoning.normal == ReasoningEffort.LOW
    assert not any(
        entry["event"] == SETTINGS_REASONING_LADDER_REPAIRED for entry in logs
    )


async def test_low_outranking_an_explicit_lower_normal_is_capped_down() -> None:
    # An operator explicitly set normal to "minimal" before the low default
    # was raised to "low": low(1) > normal(0) inverts.
    reader = _FakeReader({"reasoning_effort_normal": "minimal"})

    with structlog.testing.capture_logs() as logs:
        config = await resolve_capability_policy_config(reader)

    assert config.reasoning.low == ReasoningEffort.MINIMAL
    assert config.reasoning.normal == ReasoningEffort.MINIMAL
    entry = next(e for e in logs if e["event"] == SETTINGS_REASONING_LADDER_REPAIRED)
    assert entry["resolved_low"] == "low"
    assert entry["repaired_low"] == "minimal"


async def test_the_operators_higher_stakes_choice_is_never_touched() -> None:
    reader = _FakeReader(
        {"reasoning_effort_normal": "minimal", "reasoning_effort_high": "medium"}
    )
    config = await resolve_capability_policy_config(reader)
    assert config.reasoning.high == ReasoningEffort.MEDIUM
    assert config.reasoning.critical == ReasoningEffort.HIGH


async def test_an_unset_normal_ranks_below_every_tier_and_caps_low_to_none() -> None:
    reader = _FakeReader({"reasoning_effort_normal": "none"})
    config = await resolve_capability_policy_config(reader)
    assert config.reasoning.low is None
    assert config.reasoning.normal is None


async def test_a_cascading_inversion_repairs_every_lower_tier() -> None:
    # critical explicitly lowered to "minimal": every tier above it in stakes
    # order must cap down to match, low first.
    reader = _FakeReader(
        {
            "reasoning_effort_low": "low",
            "reasoning_effort_normal": "low",
            "reasoning_effort_high": "medium",
            "reasoning_effort_critical": "minimal",
        }
    )
    config = await resolve_capability_policy_config(reader)
    assert config.reasoning.low == ReasoningEffort.MINIMAL
    assert config.reasoning.normal == ReasoningEffort.MINIMAL
    assert config.reasoning.high == ReasoningEffort.MINIMAL
    assert config.reasoning.critical == ReasoningEffort.MINIMAL
