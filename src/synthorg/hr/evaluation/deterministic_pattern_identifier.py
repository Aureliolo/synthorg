"""Deterministic threshold-counting pattern identifier.

The shipped default :class:`PatternIdentifier`: it counts, per pillar, the
distinct agents scoring below the configured weakness threshold and emits
a ``"weakness:<pillar>"`` token for each pillar whose weak-agent count
clears ``pattern_min_agents``. Output is stable (sorted by weak-count
descending, then pillar name) so a cycle's patterns are reproducible.

This strategy is also the fallback the provider-backed identifier degrades
to when no model is available or its call fails.
"""

from synthorg.core.types import NotBlankStr
from synthorg.hr.evaluation.config import EvalLoopConfig
from synthorg.hr.evaluation.models import EvaluationReport
from synthorg.observability import get_logger
from synthorg.observability.events.eval_loop import EVAL_LOOP_PATTERN_IDENTIFIED

logger = get_logger(__name__)


class DeterministicPatternIdentifier:
    """Counts weak agents per pillar against the configured thresholds."""

    __slots__ = ("_config",)

    def __init__(self, config: EvalLoopConfig) -> None:
        self._config = config

    async def identify(
        self,
        reports: tuple[EvaluationReport, ...],
    ) -> tuple[NotBlankStr, ...]:
        """Identify pillar-weakness patterns across agents.

        Returns:
            Patterns in the form ``"weakness:<pillar>"``, ordered by
            weak-agent count (desc) then pillar name (asc).
        """
        if not self._config.pattern_identifier_enabled or not reports:
            return ()

        global_threshold = self._config.pattern_weakness_threshold
        per_pillar = self._config.pattern_thresholds
        # Track unique weak agents per pillar so the count reflects
        # distinct agents weak on a pillar, not per-pillar score entries
        # (defends against duplicate pillar entries and the same agent
        # producing multiple reports in the window).
        weak_agents_per_pillar: dict[str, set[str]] = {}
        for report in reports:
            weak_pillars = {
                score.pillar.value
                for score in report.pillar_scores
                if score.score < per_pillar.get(score.pillar.value, global_threshold)
            }
            for pillar in weak_pillars:
                weak_agents_per_pillar.setdefault(pillar, set()).add(report.agent_id)

        min_agents = self._config.pattern_min_agents
        qualifying = [
            (pillar, len(agents))
            for pillar, agents in weak_agents_per_pillar.items()
            if len(agents) >= min_agents
        ]
        qualifying.sort(key=lambda item: (-item[1], item[0]))

        patterns = tuple(NotBlankStr(f"weakness:{pillar}") for pillar, _ in qualifying)
        if patterns:
            logger.info(
                EVAL_LOOP_PATTERN_IDENTIFIED,
                pattern_count=len(patterns),
                patterns=list(patterns),
                global_threshold=global_threshold,
                per_pillar_overrides=len(per_pillar),
                min_agents=min_agents,
            )
        return patterns
