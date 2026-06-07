"""Unit tests for training mode protocol interfaces."""

from datetime import UTC, datetime

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.hr.seniority import SeniorityLevel
from synthorg.hr.training.models import (
    ContentType,
    TrainingGuardDecision,
    TrainingItem,
    TrainingPlan,
)
from synthorg.hr.training.protocol import (
    ContentExtractor,
    CurationStrategy,
    SourceSelector,
    TrainingGuard,
)


def _now() -> datetime:
    return datetime.now(UTC)


# -- Stub implementations for protocol verification -------------------


class _StubExtractor:
    """Stub extractor for protocol tests."""

    @property
    def content_type(self) -> ContentType:
        return ContentType.PROCEDURAL

    async def extract(
        self,
        *,
        source_agent_ids: tuple[NotBlankStr, ...],
        new_agent_role: NotBlankStr,
        new_agent_level: SeniorityLevel,
    ) -> tuple[TrainingItem, ...]:
        return ()


class _StubSelector:
    """Stub selector for protocol tests."""

    @property
    def name(self) -> str:
        return "stub"

    async def select(
        self,
        *,
        new_agent_role: NotBlankStr,
        new_agent_level: SeniorityLevel,
        new_agent_department: NotBlankStr | None = None,
    ) -> tuple[NotBlankStr, ...]:
        return ()


class _StubCuration:
    """Stub curation for protocol tests."""

    @property
    def name(self) -> str:
        return "stub"

    async def curate(
        self,
        items: tuple[TrainingItem, ...],
        *,
        new_agent_role: NotBlankStr,
        new_agent_level: SeniorityLevel,
        content_type: ContentType,
    ) -> tuple[TrainingItem, ...]:
        return items


class _StubGuard:
    """Stub guard for protocol tests."""

    @property
    def name(self) -> str:
        return "stub"

    async def evaluate(
        self,
        items: tuple[TrainingItem, ...],
        *,
        content_type: ContentType,
        plan: TrainingPlan,
    ) -> TrainingGuardDecision:
        return TrainingGuardDecision(
            approved_items=items,
            rejected_count=0,
            guard_name="stub",
        )


# -- Protocol conformance tests ---------------------------------------


@pytest.mark.unit
class TestContentExtractorProtocol:
    """ContentExtractor protocol conformance tests."""

    def test_stub_is_instance(self) -> None:
        assert isinstance(_StubExtractor(), ContentExtractor)

    async def test_stub_extract_returns_empty(self) -> None:
        extractor = _StubExtractor()
        result = await extractor.extract(
            source_agent_ids=("agent-1",),
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
        )
        assert result == ()

    def test_stub_content_type(self) -> None:
        assert _StubExtractor().content_type == ContentType.PROCEDURAL


@pytest.mark.unit
class TestSourceSelectorProtocol:
    """SourceSelector protocol conformance tests."""

    def test_stub_is_instance(self) -> None:
        assert isinstance(_StubSelector(), SourceSelector)

    async def test_stub_select_returns_empty(self) -> None:
        selector = _StubSelector()
        result = await selector.select(
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
        )
        assert result == ()

    def test_stub_name(self) -> None:
        assert _StubSelector().name == "stub"


@pytest.mark.unit
class TestCurationStrategyProtocol:
    """CurationStrategy protocol conformance tests."""

    def test_stub_is_instance(self) -> None:
        assert isinstance(_StubCuration(), CurationStrategy)

    async def test_stub_curate_passthrough(self) -> None:
        curation = _StubCuration()
        item = TrainingItem(
            source_agent_id="senior-1",
            content_type=ContentType.PROCEDURAL,
            content="test content",
            created_at=_now(),
        )
        result = await curation.curate(
            (item,),
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
            content_type=ContentType.PROCEDURAL,
        )
        assert result == (item,)

    def test_stub_name(self) -> None:
        assert _StubCuration().name == "stub"


@pytest.mark.unit
class TestTrainingGuardProtocol:
    """TrainingGuard protocol conformance tests."""

    def test_stub_is_instance(self) -> None:
        assert isinstance(_StubGuard(), TrainingGuard)

    async def test_stub_evaluate_approves_all(self) -> None:
        guard = _StubGuard()
        item = TrainingItem(
            source_agent_id="senior-1",
            content_type=ContentType.PROCEDURAL,
            content="test content",
            created_at=_now(),
        )
        plan = TrainingPlan(
            new_agent_id="new-1",
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
            created_at=_now(),
        )
        decision = await guard.evaluate(
            (item,),
            content_type=ContentType.PROCEDURAL,
            plan=plan,
        )
        assert len(decision.approved_items) == 1
        assert decision.rejected_count == 0

    def test_stub_name(self) -> None:
        assert _StubGuard().name == "stub"
