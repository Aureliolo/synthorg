"""Static-table fix proposer.

The shipped default :class:`FixProposer`: it maps each weakness pattern to
a remediation action id via a per-pillar table, with operator overrides
through ``EvalLoopConfig.pattern_action_map``. An import-time guard keeps
the table aligned with :class:`EvaluationPillar` so a new pillar cannot
silently drop its action.

This strategy is also the fallback the provider-backed proposer degrades
to when no model is available or its call fails.
"""

from types import MappingProxyType
from typing import Final

from synthorg.core.types import NotBlankStr
from synthorg.hr.evaluation.config import EvalLoopConfig
from synthorg.hr.evaluation.enums import EvaluationPillar
from synthorg.hr.evaluation.pattern_protocols import ProposedAction
from synthorg.observability import get_logger
from synthorg.observability.events.eval_loop import (
    EVAL_LOOP_ACTION_PROPOSED,
    EVAL_LOOP_CONFIG_DRIFT,
)

logger = get_logger(__name__)

# Keys are pillar values (matching ``EvalLoopConfig.pattern_action_map``);
# values are non-blank action ids.
DEFAULT_PATTERN_ACTIONS: Final[MappingProxyType[str, NotBlankStr]] = MappingProxyType(
    {
        EvaluationPillar.INTELLIGENCE.value: NotBlankStr("increase_review_depth"),
        EvaluationPillar.EFFICIENCY.value: NotBlankStr("tighten_cost_budget"),
        EvaluationPillar.RESILIENCE.value: NotBlankStr("add_recovery_training"),
        EvaluationPillar.GOVERNANCE.value: NotBlankStr("expand_audit_coverage"),
        EvaluationPillar.EXPERIENCE.value: NotBlankStr("improve_tone_training"),
    },
)

# Fail-fast drift guard: a new pillar without a default action raises at
# import (equivalent of a unit test exercised every load).
_EXPECTED_PATTERN_KEYS: Final[frozenset[str]] = frozenset(
    p.value for p in EvaluationPillar
)
if set(DEFAULT_PATTERN_ACTIONS.keys()) != _EXPECTED_PATTERN_KEYS:
    _missing = _EXPECTED_PATTERN_KEYS - set(DEFAULT_PATTERN_ACTIONS.keys())
    _extra = set(DEFAULT_PATTERN_ACTIONS.keys()) - _EXPECTED_PATTERN_KEYS
    _msg = (
        "DEFAULT_PATTERN_ACTIONS drifted from EvaluationPillar enum: "
        f"missing={sorted(_missing)!r}, extra={sorted(_extra)!r}"
    )
    logger.error(
        EVAL_LOOP_CONFIG_DRIFT,
        reason="default_pattern_actions_drift",
        missing=sorted(_missing),
        extra=sorted(_extra),
    )
    raise ImportError(_msg)

# Pattern kinds this proposer understands; an unknown prefix is logged
# and skipped so a drifted detector cannot emit bogus actions.
SUPPORTED_PATTERN_KINDS: Final[frozenset[str]] = frozenset({"weakness"})


def classify_pattern(
    pattern: str,
    override: dict[str, NotBlankStr],
) -> tuple[str, NotBlankStr | None, dict[str, str]]:
    """Map a pattern token to ``(reason, mapped_action, extra_log_fields)``.

    Returns ``mapped_action=None`` with a non-empty ``reason`` for every
    skip path (malformed / unknown kind / unmapped).

    Returns:
        Tuple ``(reason, mapped_action_or_None, extra_log_fields)``.
    """
    if ":" not in pattern:
        return ("malformed_pattern", None, {})
    kind, pillar = pattern.split(":", 1)
    if kind not in SUPPORTED_PATTERN_KINDS:
        return ("unknown_pattern_kind", None, {"kind": kind})
    mapped = override.get(pillar) or DEFAULT_PATTERN_ACTIONS.get(pillar)
    if not mapped:
        return ("unmapped_pattern", None, {"pillar": pillar})
    return ("", mapped, {})


class TableFixProposer:
    """Maps weakness patterns to action ids via the static table."""

    __slots__ = ("_config",)

    def __init__(self, config: EvalLoopConfig) -> None:
        self._config = config

    async def propose(
        self,
        patterns: tuple[NotBlankStr, ...],
    ) -> tuple[ProposedAction, ...]:
        """Map identified patterns to remediation actions.

        Returns:
            Ordered, de-duplicated actions, each carrying the originating
            pattern(s). An action proposed by several patterns accumulates
            all of them (first-seen order).
        """
        if not patterns:
            return ()

        override = self._config.pattern_action_map or {}
        # Insertion-ordered map preserves first-seen action order while
        # accumulating every pattern that proposed each action, so the
        # dispatcher can attribute the alert to the right weakness(es).
        action_patterns: dict[NotBlankStr, list[NotBlankStr]] = {}
        for pattern in patterns:
            reason, mapped, extra = classify_pattern(pattern, override)
            if mapped is None:
                logger.warning(
                    EVAL_LOOP_ACTION_PROPOSED,
                    action_count=0,
                    reason=reason,
                    pattern=pattern,
                    **extra,
                )
                continue
            action_patterns.setdefault(mapped, []).append(pattern)

        proposed = tuple(
            ProposedAction(action_id=action, patterns=tuple(pats))
            for action, pats in action_patterns.items()
        )
        if proposed:
            logger.info(
                EVAL_LOOP_ACTION_PROPOSED,
                action_count=len(proposed),
                actions=[pa.action_id for pa in proposed],
            )
        return proposed
