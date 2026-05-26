"""Composite quality scoring strategy (D2 Layers 1+2+3).

Combines CI signal (Layer 1), LLM judge (Layer 2), and human
override (Layer 3) into a single ``QualityScoringStrategy``.
Human override has the highest priority and short-circuits
the other layers.
"""

import asyncio
import math
from typing import TYPE_CHECKING, Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.models import QualityScoreResult
from synthorg.observability import get_logger
from synthorg.observability.events.performance import (
    PERF_COMPOSITE_SCORED,
    PERF_LLM_JUDGE_FAILED,
    PERF_QUALITY_OVERRIDE_APPLIED,
)
from synthorg.providers.resilience.errors import RetryExhaustedError

if TYPE_CHECKING:
    from synthorg.core.task import AcceptanceCriterion
    from synthorg.hr.performance.models import TaskMetricRecord
    from synthorg.hr.performance.quality_override_store import (
        QualityOverrideStore,
    )
    from synthorg.hr.performance.quality_protocol import QualityScoringStrategy

logger = get_logger(__name__)

_DEFAULT_CI_WEIGHT: Final[float] = 0.4
_DEFAULT_LLM_WEIGHT: Final[float] = 0.6
_DEFAULT_CONFIDENCE_DISCOUNT: Final[float] = 0.9
_DEFAULT_CI_ONLY_CONFIDENCE_DISCOUNT: Final[float] = 0.7


class CompositeQualityStrategy:
    """Composite quality scoring combining multiple layers.

    Evaluation order:
        1. Human override (Layer 3) -- if active, return immediately.
        2. LLM judge (Layer 2) -- if configured and succeeds.
        3. CI signal (Layer 1) -- always runs.

    When both CI and LLM layers succeed, scores are combined using
    configurable weights.  When only CI succeeds (LLM not configured
    or failed), the CI score is used directly with reduced confidence.

    When only CI is available (LLM not configured or failed), its
    confidence is reduced by ``ci_only_confidence_discount`` (default
    0.7) to signal lower scoring reliability.

    Args:
        ci_strategy: CI signal scoring strategy (Layer 1).
        llm_strategy: LLM judge scoring strategy (Layer 2, optional).
        override_store: Quality override store (Layer 3, optional).
        ci_weight: Weight for CI signal (default 0.4). Must be
            non-negative and finite. Together with ``llm_weight``
            must sum to 1.0 (within 1e-6 tolerance).
        llm_weight: Weight for LLM judge (default 0.6). Must be
            non-negative and finite. Together with ``ci_weight``
            must sum to 1.0 (within 1e-6 tolerance).
        confidence_discount: Multiplier applied to min(ci, llm)
            confidence when both layers contribute (default 0.9).
            Must be finite and in [0.0, 1.0].
        ci_only_confidence_discount: Multiplier applied to CI
            confidence when only CI is available (default 0.7).
            Must be finite and in [0.0, 1.0].
    """

    _WEIGHT_TOLERANCE: float = 1e-6

    def __init__(  # noqa: PLR0913
        self,
        *,
        ci_strategy: QualityScoringStrategy,
        llm_strategy: QualityScoringStrategy | None = None,
        override_store: QualityOverrideStore | None = None,
        ci_weight: float = _DEFAULT_CI_WEIGHT,
        llm_weight: float = _DEFAULT_LLM_WEIGHT,
        confidence_discount: float = _DEFAULT_CONFIDENCE_DISCOUNT,
        ci_only_confidence_discount: float = _DEFAULT_CI_ONLY_CONFIDENCE_DISCOUNT,
    ) -> None:
        if not math.isfinite(ci_weight) or not math.isfinite(llm_weight):
            msg = (
                f"Weights must be finite, got "
                f"ci_weight={ci_weight}, llm_weight={llm_weight}"
            )
            raise ValueError(msg)
        if ci_weight < 0.0 or llm_weight < 0.0:
            msg = (
                f"Weights must be non-negative, got "
                f"ci_weight={ci_weight}, llm_weight={llm_weight}"
            )
            raise ValueError(msg)
        if abs(ci_weight + llm_weight - 1.0) > self._WEIGHT_TOLERANCE:
            msg = f"ci_weight + llm_weight must equal 1.0, got {ci_weight + llm_weight}"
            raise ValueError(msg)
        for name, val in (
            ("confidence_discount", confidence_discount),
            ("ci_only_confidence_discount", ci_only_confidence_discount),
        ):
            if not math.isfinite(val) or val < 0.0 or val > 1.0:
                msg = f"{name} must be finite and in [0.0, 1.0], got {val}"
                raise ValueError(msg)
        self._ci_strategy = ci_strategy
        self._llm_strategy = llm_strategy
        self._override_store = override_store
        self._ci_weight = ci_weight
        self._llm_weight = llm_weight
        self._confidence_discount = confidence_discount
        self._ci_only_confidence_discount = ci_only_confidence_discount

    @property
    def name(self) -> str:
        """Human-readable strategy name."""
        return "composite"

    async def score(
        self,
        *,
        agent_id: NotBlankStr,
        task_id: NotBlankStr,
        task_result: TaskMetricRecord,
        acceptance_criteria: tuple[AcceptanceCriterion, ...],
    ) -> QualityScoreResult:
        """Score task quality using the composite layer stack.

        Args:
            agent_id: Agent who completed the task.
            task_id: Task identifier.
            task_result: Recorded task metrics.
            acceptance_criteria: Criteria to evaluate against.

        Returns:
            Quality score result with breakdown and confidence.
        """
        # Layer 3: Human override (highest priority).
        override_result = self._check_override(agent_id)
        if override_result is not None:
            return override_result

        # Layers 1+2: CI signal and LLM judge in parallel.
        # Skip layers with zero weight to avoid unnecessary calls.
        if self._ci_weight > 0.0 and self._llm_weight > 0.0:
            try:
                async with asyncio.TaskGroup() as tg:
                    ci_task = tg.create_task(
                        self._ci_strategy.score(
                            agent_id=agent_id,
                            task_id=task_id,
                            task_result=task_result,
                            acceptance_criteria=acceptance_criteria,
                        ),
                    )
                    llm_task = tg.create_task(
                        self._try_llm(
                            agent_id=agent_id,
                            task_id=task_id,
                            task_result=task_result,
                            acceptance_criteria=acceptance_criteria,
                        ),
                    )
            except* MemoryError as eg:
                raise eg.exceptions[0] from eg
            except* RecursionError as eg:
                raise eg.exceptions[0] from eg
            except* RetryExhaustedError as eg:
                # Unwrap from ExceptionGroup so engine fallback
                # chain receives a bare RetryExhaustedError.
                raise eg.exceptions[0] from eg
            ci_result = ci_task.result()
            llm_result = llm_task.result()
        elif self._ci_weight > 0.0:
            ci_result = await self._ci_strategy.score(
                agent_id=agent_id,
                task_id=task_id,
                task_result=task_result,
                acceptance_criteria=acceptance_criteria,
            )
            llm_result = None
        else:
            # ci_weight == 0.0: LLM-only mode.
            llm_result = await self._try_llm(
                agent_id=agent_id,
                task_id=task_id,
                task_result=task_result,
                acceptance_criteria=acceptance_criteria,
            )
            if llm_result is not None:
                return QualityScoreResult(
                    score=llm_result.score,
                    strategy_name=NotBlankStr(self.name),
                    breakdown=llm_result.breakdown,
                    confidence=llm_result.confidence,
                )
            # LLM failed and CI is disabled -- return zero-confidence
            # fallback so downstream knows scoring was inconclusive.
            return QualityScoreResult(
                score=0.0,
                strategy_name=NotBlankStr(self.name),
                breakdown=(),
                confidence=0.0,
            )

        # Combine layers.
        return self._combine(ci_result, llm_result)

    def _check_override(
        self,
        agent_id: NotBlankStr,
    ) -> QualityScoreResult | None:
        """Check for an active human override.

        Returns:
            Override result if active, ``None`` otherwise.
        """
        if self._override_store is None:
            return None

        override = self._override_store.get_active_override(agent_id)
        if override is None:
            return None

        logger.info(
            PERF_QUALITY_OVERRIDE_APPLIED,
            agent_id=agent_id,
            score=override.score,
            applied_by=override.applied_by,
        )
        return QualityScoreResult(
            score=override.score,
            strategy_name=NotBlankStr("human_override"),
            breakdown=(("human_override", override.score),),
            confidence=1.0,
        )

    async def _try_llm(
        self,
        *,
        agent_id: NotBlankStr,
        task_id: NotBlankStr,
        task_result: TaskMetricRecord,
        acceptance_criteria: tuple[AcceptanceCriterion, ...],
    ) -> QualityScoreResult | None:
        """Attempt LLM judge scoring.

        Returns ``None`` if the LLM strategy is not configured,
        encounters a non-critical failure, or returns zero confidence.
        ``MemoryError``, ``RecursionError``, and
        ``RetryExhaustedError`` are re-raised.

        Returns:
            The resulting ``QualityScoreResult``, or ``None`` when unavailable.

        Raises:
            RetryExhaustedError: If the relevant budget or quota is exhausted.
        """
        if self._llm_strategy is None:
            return None

        try:
            result = await self._llm_strategy.score(
                agent_id=agent_id,
                task_id=task_id,
                task_result=task_result,
                acceptance_criteria=acceptance_criteria,
            )
        except RetryExhaustedError:
            raise
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                PERF_LLM_JUDGE_FAILED,
                agent_id=agent_id,
                task_id=task_id,
                note="llm_strategy_failed",
            )
            return None

        # Zero confidence means the LLM judge failed gracefully.
        if result.confidence == 0.0:
            return None

        return result

    def _combine(
        self,
        ci_result: QualityScoreResult,
        llm_result: QualityScoreResult | None,
    ) -> QualityScoreResult:
        """Combine CI and optional LLM scores.

        When both layers are available, applies weighted combination.
        When only CI is available, uses the CI score directly with
        reduced confidence.

        Returns:
            Result of type ``QualityScoreResult``.
        """
        if llm_result is not None:
            # Weighted combination.
            combined_score = (
                ci_result.score * self._ci_weight + llm_result.score * self._llm_weight
            )
            combined_score = round(
                max(0.0, min(10.0, combined_score)),
                4,
            )
            confidence = round(
                min(ci_result.confidence, llm_result.confidence)
                * self._confidence_discount,
                4,
            )
            breakdown: tuple[tuple[NotBlankStr, float], ...] = (
                (NotBlankStr("ci_signal"), ci_result.score),
                (NotBlankStr("llm_judge"), llm_result.score),
            )
        else:
            # CI-only fallback.
            combined_score = round(ci_result.score, 4)
            confidence = round(
                ci_result.confidence * self._ci_only_confidence_discount,
                4,
            )
            breakdown = ((NotBlankStr("ci_signal"), ci_result.score),)

        result = QualityScoreResult(
            score=combined_score,
            strategy_name=NotBlankStr(self.name),
            breakdown=breakdown,
            confidence=confidence,
        )

        logger.debug(
            PERF_COMPOSITE_SCORED,
            score=result.score,
            confidence=result.confidence,
            layers=len(breakdown),
        )
        return result
