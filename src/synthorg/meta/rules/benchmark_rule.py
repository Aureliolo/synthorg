# module-kind: code
"""Golden-benchmark regression rule for the meta-loop.

Split from :mod:`synthorg.meta.rules.builtin` to keep that module under
its size budget. The golden-company benchmark is the org's ground-truth
quality signal, so its regression rule lives in its own module alongside
the rest of the benchmark-feedback wiring.
"""

from typing import Final

from synthorg.core.types import NotBlankStr
from synthorg.meta.models import (
    OrgSignalSnapshot,
    ProposalAltitude,
    RuleMatch,
    RuleSeverity,
)

# A regression needs a predecessor run to compare against, so the rule
# only fires once at least this many runs are on the curve.
_BENCHMARK_REGRESSION_MIN_RUNS: Final[int] = 2


class BenchmarkRegressionRule:
    """Fires CRITICAL when the latest golden-benchmark run regressed.

    The golden-company benchmark is the org's ground-truth quality
    signal, so a regression -- the latest scored run dropping materially
    below its predecessor -- is the strongest "something got worse"
    signal available and warrants the highest severity. It suggests
    prompt-tuning and code-modification remediations, the altitudes that
    can move a benchmark score back up.
    """

    @property
    def name(self) -> NotBlankStr:
        """Rule name.

        Returns:
            ``NotBlankStr`` instance.
        """
        return NotBlankStr("benchmark_regression")

    @property
    def target_altitudes(self) -> tuple[ProposalAltitude, ...]:
        """Suggests prompt tuning and code modification.

        Returns:
            Tuple of the declared element types.
        """
        return (
            ProposalAltitude.PROMPT_TUNING,
            ProposalAltitude.CODE_MODIFICATION,
        )

    def evaluate(self, snapshot: OrgSignalSnapshot) -> RuleMatch | None:
        """Check whether the latest benchmark run regressed.

        Returns:
            The ``RuleMatch`` value when present, ``None`` otherwise.
        """
        bench = snapshot.benchmark
        if bench.run_count < _BENCHMARK_REGRESSION_MIN_RUNS:
            return None
        if not bench.is_regression:
            return None
        return RuleMatch(
            rule_name=self.name,
            severity=RuleSeverity.CRITICAL,
            description=(
                f"Benchmark score dropped {abs(bench.delta)} points "
                f"(latest {bench.latest_total}/{bench.max_total})"
            ),
            signal_context={
                "latest_total": bench.latest_total,
                "max_total": bench.max_total,
                "delta": bench.delta,
                "run_count": bench.run_count,
            },
            suggested_altitudes=self.target_altitudes,
        )
