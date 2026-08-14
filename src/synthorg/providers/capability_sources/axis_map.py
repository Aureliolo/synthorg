# module-kind: declarative
"""Which benchmark measures which axis.

A source publishes results per benchmark, and a benchmark asks one kind of
question. Collapsing them onto three axes is what lets two sources be
compared at all, and it is a judgement rather than a fact, so it is
declared in one table instead of inferred at each call site.

**A benchmark this table does not know is not graded.** Landing it on
``general`` so no evidence is discarded is tempting and wrong: the two are
not comparable failures. A skipped row is a gap, counted in the source's
``rows_skipped`` and visible on the dashboard. A row defaulted into an
axis is a corruption:
the axis is ranked as a cohort and its members averaged, so a row landing
in the wrong one does not sit harmlessly at the edge, it moves everybody's
rank.

Fragments are matched as substrings rather than exact names because a
source renames "SWE-bench Verified" to "SWE-Bench (verified)" without
telling anyone, and an exact-match table would silently unclassify the
whole benchmark on the day it happened.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from synthorg.providers.capability_sources.models import CapabilityAxis

#: Substring matched case-insensitively against a benchmark's published
#: name. A name matching no fragment is reported as unclassified.
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
        "chess puzzles": "reasoning",
        "hle": "reasoning",
        "humanity's last exam": "reasoning",
        # Broad knowledge and language, which is what ``general`` means
        # here: a named cohort of its own, never a catch-all.
        "mmlu": "general",
        "writing": "general",
        "hellaswag": "general",
        "ifeval": "general",
        "simpleqa": "general",
    },
)


def axis_for_benchmark(benchmark_name: str) -> CapabilityAxis | None:
    """Return the axis *benchmark_name* measures.

    Args:
        benchmark_name: The benchmark's name as the source publishes it.

    Returns:
        The matching axis, or ``None`` when the name matches no known
        fragment. ``None`` means "we cannot say what this measures", and
        the caller drops the row rather than filing it under a guess.
    """
    lowered = benchmark_name.casefold()
    for fragment, axis in _AXIS_BY_BENCHMARK_FRAGMENT.items():
        if fragment in lowered:
            return axis
    return None


__all__ = ["axis_for_benchmark"]
