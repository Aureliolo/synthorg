# module-kind: declarative
"""Which benchmark measures which axis.

A source publishes results per benchmark, and a benchmark asks one kind of
question. Collapsing them onto three axes is what lets two sources be
compared at all, and it is a judgement rather than a fact, so it is
declared in one table instead of inferred at each call site.

A benchmark absent from the table lands on ``general`` rather than being
dropped. Dropping it would quietly discard evidence because we had not
classified it yet, which is the same silent-omission failure the whole
layer exists to remove; ``general`` says "this measures something" without
claiming to know what.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from synthorg.providers.capability_sources.models import CapabilityAxis

#: Substring matched case-insensitively against a benchmark's published
#: name. Substrings rather than exact names because a source renames
#: "SWE-bench Verified" to "SWE-Bench (verified)" without telling anyone,
#: and an exact-match table would silently reclassify the whole benchmark
#: to ``general`` on the day it happened.
_AXIS_BY_BENCHMARK_FRAGMENT: Final[Mapping[str, CapabilityAxis]] = MappingProxyType(
    {
        # Writing and running code, and operating a machine to do it.
        "swe-bench": "coding",
        "swebench": "coding",
        "aider": "coding",
        "terminal-bench": "coding",
        "livecodebench": "coding",
        "humaneval": "coding",
        "mbpp": "coding",
        "codeforces": "coding",
        "agent company": "coding",
        # Multi-step problems with a verifiable answer.
        "gpqa": "reasoning",
        "math": "reasoning",
        "aime": "reasoning",
        "frontiermath": "reasoning",
        "arc-agi": "reasoning",
        "hle": "reasoning",
        "humanity's last exam": "reasoning",
        # Broad knowledge and language, which is what ``general`` means
        # here rather than "everything left over".
        "mmlu": "general",
        "writing": "general",
        "hellaswag": "general",
        "ifeval": "general",
    },
)

DEFAULT_AXIS: Final[CapabilityAxis] = "general"


def axis_for_benchmark(benchmark_name: str) -> CapabilityAxis:
    """Return the axis *benchmark_name* measures.

    Args:
        benchmark_name: The benchmark's name as the source publishes it.

    Returns:
        The matching axis, or :data:`DEFAULT_AXIS` when the name matches
        no known fragment.
    """
    lowered = benchmark_name.casefold()
    for fragment, axis in _AXIS_BY_BENCHMARK_FRAGMENT.items():
        if fragment in lowered:
            return axis
    return DEFAULT_AXIS


__all__ = ["DEFAULT_AXIS", "axis_for_benchmark"]
