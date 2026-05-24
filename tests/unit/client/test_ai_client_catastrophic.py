"""Catastrophic-error carve-outs in :class:`AIClient`.

Both ``submit_requirement`` and ``review_deliverable`` route broad
``Exception`` through ``log_exception_redacted`` before re-raising.
``MemoryError`` / ``RecursionError`` must escape that handler entirely so
they propagate without log-handler work (which may itself allocate or
recurse) running first.
"""

from unittest.mock import AsyncMock

import pytest

from synthorg.client.ai_client import AIClient
from synthorg.client.models import ClientProfile, GenerationContext, ReviewContext
from synthorg.client.protocols import FeedbackStrategy, RequirementGenerator
from synthorg.core.types import NotBlankStr
from tests._shared import mock_of


def _profile() -> ClientProfile:
    return ClientProfile(
        client_id=NotBlankStr("client-1"),
        name=NotBlankStr("client-1"),
        persona="Test persona",
        strictness_level=0.5,
    )


@pytest.mark.unit
class TestAIClientCatastrophicErrors:
    """Catastrophic-error pass-through on the two AIClient exception paths."""

    @pytest.mark.parametrize("exc_cls", [MemoryError, RecursionError])
    async def test_generator_catastrophic_propagates(
        self,
        exc_cls: type[BaseException],
    ) -> None:
        """``generator.generate`` raising ``MemoryError`` / ``RecursionError``
        bypasses the redacted-logging handler in ``submit_requirement``."""
        generator = mock_of[RequirementGenerator](
            generate=AsyncMock(side_effect=exc_cls),
        )
        feedback = mock_of[FeedbackStrategy](evaluate=AsyncMock())
        client = AIClient(
            profile=_profile(),
            generator=generator,
            feedback=feedback,
        )
        context = GenerationContext(
            project_id=NotBlankStr("proj-1"),
            domain=NotBlankStr("test"),
            count=1,
        )
        with pytest.raises(exc_cls):
            await client.submit_requirement(context)

    @pytest.mark.parametrize("exc_cls", [MemoryError, RecursionError])
    async def test_feedback_catastrophic_propagates(
        self,
        exc_cls: type[BaseException],
    ) -> None:
        """``feedback.evaluate`` raising ``MemoryError`` / ``RecursionError``
        bypasses the redacted-logging handler in ``review_deliverable``."""
        generator = mock_of[RequirementGenerator](generate=AsyncMock())
        feedback = mock_of[FeedbackStrategy](
            evaluate=AsyncMock(side_effect=exc_cls),
        )
        client = AIClient(
            profile=_profile(),
            generator=generator,
            feedback=feedback,
        )
        context = ReviewContext(
            task_id=NotBlankStr("task-1"),
            task_title="t",
            deliverable_summary="d",
            acceptance_criteria=(),
        )
        with pytest.raises(exc_cls):
            await client.review_deliverable(context)
