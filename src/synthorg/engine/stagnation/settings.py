"""Read the operator's stagnation choices into a frozen config.

The settings registry is the one owner of these values (see
``settings/definitions/engine_stagnation.py``); this turns them into the frozen
selector ``create_stagnation_detector`` already takes.

Read twice, by the work loop and by the planning loop, because a detector
carries per-loop state and the two loops run concurrently. Two readers of one
owner, not two owners.
"""

from typing import Literal, cast

from synthorg.engine.stagnation.models import (
    QualityErosionConfig,
    StagnationConfig,
    StagnationDetectionConfig,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.resolver import ConfigResolver

_NS: str = SettingNamespace.ENGINE.value

type _Strategy = Literal["off", "tool_repetition", "quality_erosion"]


async def resolve_stagnation_config(
    resolver: ConfigResolver,
) -> StagnationDetectionConfig:
    """Build the stagnation selector from the live settings.

    The enum setting constrains the strategy to the three the selector
    declares, so the cast records what the registry already enforces rather
    than widening anything.

    Args:
        resolver: The settings resolver, honouring DB over env over default.

    Returns:
        The operator's stagnation configuration.
    """
    strategy = cast("_Strategy", await resolver.get_str(_NS, "stagnation_strategy"))
    return StagnationDetectionConfig(
        strategy=strategy,
        tool_repetition=StagnationConfig(
            window_size=await resolver.get_int(_NS, "stagnation_window_size"),
            repetition_threshold=await resolver.get_float(
                _NS, "stagnation_repetition_threshold"
            ),
            cycle_detection=await resolver.get_bool(_NS, "stagnation_cycle_detection"),
            max_corrections=await resolver.get_int(_NS, "stagnation_max_corrections"),
            min_tool_turns=await resolver.get_int(_NS, "stagnation_min_tool_turns"),
        ),
        quality_erosion=QualityErosionConfig(
            threshold=await resolver.get_float(_NS, "stagnation_erosion_threshold"),
            window_size=await resolver.get_int(_NS, "stagnation_erosion_window_size"),
        ),
    )
