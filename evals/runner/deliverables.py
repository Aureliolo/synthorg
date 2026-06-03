# module-kind: code
"""Deterministic per-brief deliverables for the benchmark's scripted provider.

The default benchmark provider is a scripted stand-in for a real LLM: it
returns a fixed deliverable per brief so the runner can grade a company's
output without LLM spend. The executable brief carries two solution variants
keyed by quality profile -- a competent solution that passes its hidden tests
and a degraded one that compiles but fails them -- so the benchmark exposes a
genuine, grader-measured quality delta rather than one that hinges solely on
the budget ceiling. The executable grader computes the grade by running the
artifact; nothing here declares a score.
"""

from typing import Final

from evals.runner.profiles import BenchmarkStrategyProfile

#: Generic clean deliverable for judged briefs (profile-independent): the
#: judged axis is scored by the calibrated judge, not the executable grader.
DEFAULT_DELIVERABLE: Final[str] = "Benchmark deliverable: the brief was addressed."

#: brief_id of the executable quality brief shipped in ``evals/briefs/``.
TEXT_NORMALISATION_BRIEF_ID: Final[str] = "text-normalisation"

# A competent solution: ``normalise`` trims and case-folds, so the hidden test
# (``normalise('  AB ') == 'ab'``) passes and the brief scores full marks.
_COMPETENT_SOLUTION: Final[str] = (
    '"""Normalise free-text tokens to a canonical comparison form."""\n'
    "\n"
    "\n"
    "def normalise(value):\n"
    "    return value.strip().lower()\n"
)

# A degraded solution: valid Python (it compiles, so build + lint pass) that
# returns the value unchanged, so the hidden test fails and the brief loses the
# hidden-test weight. The 60-point gap from the competent solution is computed
# by the grader running the artifact, not asserted here.
_DEGRADED_SOLUTION: Final[str] = (
    '"""Normalise free-text tokens to a canonical comparison form."""\n'
    "\n"
    "\n"
    "def normalise(value):\n"
    "    return value\n"
)

#: ``{brief_id: {profile: deliverable_text}}`` for briefs whose deliverable
#: varies by quality profile. Briefs absent from this map get the generic
#: clean deliverable for every profile.
BENCHMARK_DELIVERABLES: Final[dict[str, dict[BenchmarkStrategyProfile, str]]] = {
    TEXT_NORMALISATION_BRIEF_ID: {
        BenchmarkStrategyProfile.COMPETENT: _COMPETENT_SOLUTION,
        BenchmarkStrategyProfile.DEGRADED: _DEGRADED_SOLUTION,
    },
}


__all__ = [
    "BENCHMARK_DELIVERABLES",
    "DEFAULT_DELIVERABLE",
    "TEXT_NORMALISATION_BRIEF_ID",
]
