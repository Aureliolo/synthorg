"""Unit tests for the run-narrator factory."""

import pytest

from synthorg.docs_engine.service import DocsService
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.narrative.factory import (
    build_chief_of_staff_narrator,
)
from synthorg.meta.chief_of_staff.narrative.service import ChiefOfStaffNarrator
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrameRepository,
)
from synthorg.persistence.task_protocol import TaskRepository
from synthorg.project_brain.service import ProjectBrainService
from synthorg.providers.protocol import CompletionProvider
from tests._shared import mock_of
from tests._shared.model_binding import one_connection

pytestmark = pytest.mark.unit


class TestBuildNarrator:
    def test_builds_even_when_disabled(self) -> None:
        """The narrator is built regardless of the flag (gate moved per-run).

        Documentary mode is gated live in ``generate``; the factory builds the
        instance whenever its collaborators are present so the flag can flip on
        without a restart.
        """
        narrator = build_chief_of_staff_narrator(
            ChiefOfStaffConfig(),
            connections=one_connection(mock_of[CompletionProvider]()),
            docs_service=mock_of[DocsService](),
            brain_service=mock_of[ProjectBrainService](),
            frames=mock_of[FlightRecorderFrameRepository](),
            task_repo=mock_of[TaskRepository](),
        )
        assert isinstance(narrator, ChiefOfStaffNarrator)

    def test_missing_provider_returns_none(self) -> None:
        narrator = build_chief_of_staff_narrator(
            ChiefOfStaffConfig(narrative_enabled=True),
            connections=None,
            docs_service=mock_of[DocsService](),
            brain_service=mock_of[ProjectBrainService](),
            frames=mock_of[FlightRecorderFrameRepository](),
            task_repo=mock_of[TaskRepository](),
        )
        assert narrator is None

    def test_missing_docs_returns_none(self) -> None:
        narrator = build_chief_of_staff_narrator(
            ChiefOfStaffConfig(narrative_enabled=True),
            connections=one_connection(mock_of[CompletionProvider]()),
            docs_service=None,
            brain_service=mock_of[ProjectBrainService](),
            frames=mock_of[FlightRecorderFrameRepository](),
            task_repo=mock_of[TaskRepository](),
        )
        assert narrator is None

    def test_builds_when_all_present(self) -> None:
        narrator = build_chief_of_staff_narrator(
            ChiefOfStaffConfig(narrative_enabled=True),
            connections=one_connection(mock_of[CompletionProvider]()),
            docs_service=mock_of[DocsService](),
            brain_service=mock_of[ProjectBrainService](),
            frames=mock_of[FlightRecorderFrameRepository](),
            task_repo=mock_of[TaskRepository](),
        )
        assert isinstance(narrator, ChiefOfStaffNarrator)
