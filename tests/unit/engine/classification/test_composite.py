"""Tests for CompositeDetector and deduplication logic."""

from datetime import date
from uuid import uuid4

import pytest

from synthorg.budget.coordination_config import (
    DetectionScope,
    ErrorCategory,
)
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.engine.classification.composite import (
    CompositeDetector,
    deduplicate_findings,
)
from synthorg.engine.classification.models import (
    ErrorFinding,
    ErrorSeverity,
)
from synthorg.engine.classification.protocol import (
    DetectionContext,
    Detector,
)
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
)


def _identity() -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name="Composite Test Agent",
        role="Developer",
        department="Engineering",
        model=ModelConfig(
            provider="test-provider",
            model_id="test-basic-001",
        ),
        hiring_date=date(2026, 1, 1),
    )


def _context() -> DetectionContext:
    identity = _identity()
    ctx = AgentContext.from_identity(identity)
    er = ExecutionResult(
        context=ctx,
        termination_reason=TerminationReason.COMPLETED,
    )
    return DetectionContext(
        execution_result=er,
        agent_id="agent-1",
        task_id="task-1",
        scope=DetectionScope.SAME_TASK,
    )


def _finding(
    *,
    category: ErrorCategory = ErrorCategory.LOGICAL_CONTRADICTION,
    severity: ErrorSeverity = ErrorSeverity.HIGH,
    description: str = "Test finding",
    evidence: tuple[str, ...] = (),
    turn_range: tuple[int, int] | None = None,
) -> ErrorFinding:
    return ErrorFinding(
        category=category,
        severity=severity,
        description=description,
        evidence=evidence,
        turn_range=turn_range,
    )


class _FakeDetector:
    """Detector that returns pre-configured findings."""

    def __init__(
        self,
        category: ErrorCategory,
        findings: tuple[ErrorFinding, ...],
    ) -> None:
        self._category = category
        self._findings = findings

    @property
    def category(self) -> ErrorCategory:
        return self._category

    @property
    def supported_scopes(self) -> frozenset[DetectionScope]:
        return frozenset({DetectionScope.SAME_TASK})

    async def detect(
        self,
        context: DetectionContext,
    ) -> tuple[ErrorFinding, ...]:
        return self._findings


# ── deduplicate_findings ───────────────────────────────────────


@pytest.mark.unit
class TestDeduplicateFindings:
    """Deduplication logic for finding merging."""

    def test_empty_input(self) -> None:
        assert deduplicate_findings(()) == ()

    def test_no_duplicates(self) -> None:
        findings = (
            _finding(description="Finding A", turn_range=(0, 1)),
            _finding(description="Finding B", turn_range=(2, 3)),
        )
        result = deduplicate_findings(findings)
        assert len(result) == 2

    def test_exact_duplicates_merged(self) -> None:
        f = _finding(description="Same finding", turn_range=(0, 1))
        result = deduplicate_findings((f, f))
        assert len(result) == 1

    def test_higher_severity_wins(self) -> None:
        low = _finding(
            description="Issue",
            severity=ErrorSeverity.LOW,
            turn_range=(0, 1),
        )
        high = _finding(
            description="Issue",
            severity=ErrorSeverity.HIGH,
            turn_range=(0, 1),
        )
        result = deduplicate_findings((low, high))
        assert len(result) == 1
        assert result[0].severity == ErrorSeverity.HIGH

    def test_evidence_merged(self) -> None:
        a = _finding(
            description="Issue",
            evidence=("evidence A",),
            turn_range=(0, 1),
        )
        b = _finding(
            description="Issue",
            evidence=("evidence B",),
            turn_range=(0, 1),
        )
        result = deduplicate_findings((a, b))
        assert len(result) == 1
        assert "evidence A" in result[0].evidence
        assert "evidence B" in result[0].evidence

    def test_different_turn_ranges_not_merged(self) -> None:
        a = _finding(description="Issue", turn_range=(0, 1))
        b = _finding(description="Issue", turn_range=(2, 3))
        result = deduplicate_findings((a, b))
        assert len(result) == 2


# ── CompositeDetector ──────────────────────────────────────────


@pytest.mark.unit
class TestCompositeDetector:
    """CompositeDetector merging and protocol compliance."""

    def test_implements_detector_protocol(self) -> None:
        d = _FakeDetector(ErrorCategory.LOGICAL_CONTRADICTION, ())
        composite = CompositeDetector(detectors=(d,))
        assert isinstance(composite, Detector)

    def test_category_from_sub_detectors(self) -> None:
        d = _FakeDetector(ErrorCategory.NUMERICAL_DRIFT, ())
        composite = CompositeDetector(detectors=(d,))
        assert composite.category == ErrorCategory.NUMERICAL_DRIFT

    def test_scopes_union(self) -> None:
        class ScopeA:
            @property
            def category(self) -> ErrorCategory:
                return ErrorCategory.LOGICAL_CONTRADICTION

            @property
            def supported_scopes(self) -> frozenset[DetectionScope]:
                return frozenset({DetectionScope.SAME_TASK})

            async def detect(
                self, context: DetectionContext
            ) -> tuple[ErrorFinding, ...]:
                return ()

        class ScopeB:
            @property
            def category(self) -> ErrorCategory:
                return ErrorCategory.LOGICAL_CONTRADICTION

            @property
            def supported_scopes(self) -> frozenset[DetectionScope]:
                return frozenset({DetectionScope.TASK_TREE})

            async def detect(
                self, context: DetectionContext
            ) -> tuple[ErrorFinding, ...]:
                return ()

        composite = CompositeDetector(detectors=(ScopeA(), ScopeB()))
        assert composite.supported_scopes == frozenset(
            {DetectionScope.SAME_TASK, DetectionScope.TASK_TREE},
        )

    def test_empty_detectors_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            CompositeDetector(detectors=())

    def test_mixed_categories_rejected(self) -> None:
        a = _FakeDetector(ErrorCategory.LOGICAL_CONTRADICTION, ())
        b = _FakeDetector(ErrorCategory.NUMERICAL_DRIFT, ())
        with pytest.raises(ValueError, match="share a category"):
            CompositeDetector(detectors=(a, b))

    async def test_merges_findings_from_sub_detectors(self) -> None:
        f1 = _finding(description="Finding A")
        f2 = _finding(description="Finding B")
        d1 = _FakeDetector(ErrorCategory.LOGICAL_CONTRADICTION, (f1,))
        d2 = _FakeDetector(ErrorCategory.LOGICAL_CONTRADICTION, (f2,))
        composite = CompositeDetector(detectors=(d1, d2))

        ctx = _context()
        findings = await composite.detect(ctx)
        assert len(findings) == 2

    async def test_deduplicates_identical_findings(self) -> None:
        f = _finding(description="Same finding", turn_range=(0, 1))
        d1 = _FakeDetector(ErrorCategory.LOGICAL_CONTRADICTION, (f,))
        d2 = _FakeDetector(ErrorCategory.LOGICAL_CONTRADICTION, (f,))
        composite = CompositeDetector(detectors=(d1, d2))

        ctx = _context()
        findings = await composite.detect(ctx)
        assert len(findings) == 1

    async def test_single_detector(self) -> None:
        f = _finding(description="Only finding")
        d = _FakeDetector(ErrorCategory.LOGICAL_CONTRADICTION, (f,))
        composite = CompositeDetector(detectors=(d,))

        ctx = _context()
        findings = await composite.detect(ctx)
        assert len(findings) == 1

    async def test_failing_sub_detector_does_not_discard_siblings(self) -> None:
        """One sub-detector raising must never cancel the others."""

        class _RaisingDetector:
            @property
            def category(self) -> ErrorCategory:
                return ErrorCategory.LOGICAL_CONTRADICTION

            @property
            def supported_scopes(self) -> frozenset[DetectionScope]:
                return frozenset({DetectionScope.SAME_TASK})

            async def detect(
                self, context: DetectionContext
            ) -> tuple[ErrorFinding, ...]:
                msg = "simulated provider failure"
                raise RuntimeError(msg)

        healthy = _FakeDetector(
            ErrorCategory.LOGICAL_CONTRADICTION,
            (_finding(description="Survivor"),),
        )
        composite = CompositeDetector(detectors=(_RaisingDetector(), healthy))
        findings = await composite.detect(_context())
        assert len(findings) == 1
        assert findings[0].description == "Survivor"

    async def test_scope_filter_skips_incompatible_detectors(self) -> None:
        """Sub-detectors whose scopes exclude ``context.scope`` are skipped."""

        class _SameTaskOnly:
            @property
            def category(self) -> ErrorCategory:
                return ErrorCategory.LOGICAL_CONTRADICTION

            @property
            def supported_scopes(self) -> frozenset[DetectionScope]:
                return frozenset({DetectionScope.SAME_TASK})

            async def detect(
                self, context: DetectionContext
            ) -> tuple[ErrorFinding, ...]:
                return (_finding(description="same-task only"),)

        class _TaskTreeOnly:
            def __init__(self) -> None:
                self.called = False

            @property
            def category(self) -> ErrorCategory:
                return ErrorCategory.LOGICAL_CONTRADICTION

            @property
            def supported_scopes(self) -> frozenset[DetectionScope]:
                return frozenset({DetectionScope.TASK_TREE})

            async def detect(
                self, context: DetectionContext
            ) -> tuple[ErrorFinding, ...]:
                self.called = True
                return (_finding(description="task-tree only"),)

        task_tree_only = _TaskTreeOnly()
        composite = CompositeDetector(
            detectors=(_SameTaskOnly(), task_tree_only),
        )
        # context.scope is SAME_TASK; the TASK_TREE-only detector
        # must be filtered out and never invoked.
        ctx = _context()
        assert ctx.scope == DetectionScope.SAME_TASK
        findings = await composite.detect(ctx)
        assert len(findings) == 1
        assert findings[0].description == "same-task only"
        assert task_tree_only.called is False

    async def test_scope_filter_skips_all_returns_empty(self) -> None:
        """A composite with no eligible detectors returns empty."""

        class _TaskTreeOnly:
            @property
            def category(self) -> ErrorCategory:
                return ErrorCategory.LOGICAL_CONTRADICTION

            @property
            def supported_scopes(self) -> frozenset[DetectionScope]:
                return frozenset({DetectionScope.TASK_TREE})

            async def detect(
                self, context: DetectionContext
            ) -> tuple[ErrorFinding, ...]:
                return (_finding(description="should not run"),)

        composite = CompositeDetector(detectors=(_TaskTreeOnly(),))
        findings = await composite.detect(_context())
        assert findings == ()
