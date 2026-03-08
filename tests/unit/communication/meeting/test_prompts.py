"""Tests for shared prompt builders."""

import pytest

from ai_company.communication.meeting._prompts import build_agenda_prompt
from ai_company.communication.meeting.models import MeetingAgenda, MeetingAgendaItem

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestBuildAgendaPrompt:
    """Tests for build_agenda_prompt."""

    def test_minimal_agenda(self) -> None:
        agenda = MeetingAgenda(title="Sprint Planning")
        result = build_agenda_prompt(agenda)
        assert result == "Meeting: Sprint Planning"

    def test_agenda_with_context(self) -> None:
        agenda = MeetingAgenda(
            title="Design Review",
            context="Reviewing the API design",
        )
        result = build_agenda_prompt(agenda)
        assert "Meeting: Design Review" in result
        assert "Context: Reviewing the API design" in result

    def test_agenda_without_context(self) -> None:
        agenda = MeetingAgenda(title="Standup")
        result = build_agenda_prompt(agenda)
        assert "Context:" not in result

    def test_agenda_with_items(self) -> None:
        items = (
            MeetingAgendaItem(
                title="API Design",
                description="Discuss REST API structure",
            ),
            MeetingAgendaItem(title="Testing Strategy"),
        )
        agenda = MeetingAgenda(
            title="Sprint Planning",
            context="Sprint 42",
            items=items,
        )
        result = build_agenda_prompt(agenda)
        assert "Agenda items:" in result
        assert "1. API Design" in result
        assert "Discuss REST API structure" in result
        assert "2. Testing Strategy" in result

    def test_agenda_without_items(self) -> None:
        agenda = MeetingAgenda(title="Open Discussion")
        result = build_agenda_prompt(agenda)
        assert "Agenda items:" not in result

    def test_items_without_descriptions(self) -> None:
        items = (
            MeetingAgendaItem(title="Topic A"),
            MeetingAgendaItem(title="Topic B"),
        )
        agenda = MeetingAgenda(title="Quick Sync", items=items)
        result = build_agenda_prompt(agenda)
        assert "1. Topic A" in result
        assert "2. Topic B" in result
        # No em dash separator when no description
        assert " — " not in result

    def test_items_with_descriptions_use_em_dash(self) -> None:
        items = (MeetingAgendaItem(title="Auth", description="OAuth flow"),)
        agenda = MeetingAgenda(title="Design", items=items)
        result = build_agenda_prompt(agenda)
        assert "1. Auth — OAuth flow" in result

    def test_items_with_presenter_id(self) -> None:
        """Presenter ID is included in the formatted prompt."""
        items = (
            MeetingAgendaItem(
                title="API Design",
                description="REST endpoints",
                presenter_id="lead-dev",
            ),
        )
        agenda = MeetingAgenda(title="Review", items=items)
        result = build_agenda_prompt(agenda)
        assert "(presenter: lead-dev)" in result

    def test_items_without_presenter_id(self) -> None:
        """No presenter tag when presenter_id is None."""
        items = (MeetingAgendaItem(title="Topic"),)
        agenda = MeetingAgenda(title="Sync", items=items)
        result = build_agenda_prompt(agenda)
        assert "presenter:" not in result
