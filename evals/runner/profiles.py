# module-kind: code
"""Quality profile for the benchmark's scripted strategy.

A synthorg-free leaf module: kept apart from :mod:`evals.runner.strategies` (which
imports the provider SDK) so importing the profile never drags the provider import
graph in. The runner pairs a company config with a profile as an explicit
benchmark-harness knob, kept out of the product config.
"""

from enum import StrEnum


class BenchmarkStrategyProfile(StrEnum):
    """Quality profile the scripted strategy renders a deliverable at.

    An explicit benchmark-harness knob (kept out of the product config): the
    runner pairs a company config with a profile so the executable brief's hidden
    tests pass (``COMPETENT``) or fail (``DEGRADED``). The claim it backs is "the
    executable grader discriminates passing from failing deliverables end to end",
    not "a healthy config causes higher quality".
    """

    COMPETENT = "competent"
    DEGRADED = "degraded"


__all__ = ["BenchmarkStrategyProfile"]
