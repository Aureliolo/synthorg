"""Tests for meeting LLM response parsing helpers."""

import pytest

from ai_company.communication.meeting._parsing import (
    parse_action_items,
    parse_decisions,
)

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestParseDecisions:
    def test_numbered_list(self) -> None:
        text = (
            "# Decisions\n1. Use async for all I/O\n2. Adopt event sourcing pattern\n"
        )
        result = parse_decisions(text)
        assert result == (
            "Use async for all I/O",
            "Adopt event sourcing pattern",
        )

    def test_bulleted_list(self) -> None:
        text = "## Decisions\n- Refactor the module\n- Add integration tests\n"
        result = parse_decisions(text)
        assert result == (
            "Refactor the module",
            "Add integration tests",
        )

    def test_colon_header(self) -> None:
        text = "Decisions:\n- First decision\n- Second decision\n"
        result = parse_decisions(text)
        assert result == ("First decision", "Second decision")

    def test_no_decisions_section(self) -> None:
        text = "This is a summary with no decisions section."
        result = parse_decisions(text)
        assert result == ()

    def test_empty_string(self) -> None:
        result = parse_decisions("")
        assert result == ()

    def test_section_stops_at_next_header(self) -> None:
        text = "# Decisions\n1. Do X\n# Action Items\n1. Assign Y\n"
        result = parse_decisions(text)
        assert result == ("Do X",)

    def test_asterisk_bullets(self) -> None:
        text = "# Decisions\n* Star item\n"
        result = parse_decisions(text)
        assert result == ("Star item",)

    def test_whitespace_only_items_skipped(self) -> None:
        text = "# Decisions\n1.   \n2. Real decision\n"
        result = parse_decisions(text)
        assert result == ("Real decision",)

    def test_case_insensitive_header(self) -> None:
        text = "# DECISIONS\n- Case test\n"
        result = parse_decisions(text)
        assert result == ("Case test",)


@pytest.mark.unit
class TestParseActionItems:
    def test_simple_action_items(self) -> None:
        text = "# Action Items\n1. Write unit tests\n2. Update documentation\n"
        result = parse_action_items(text)
        assert len(result) == 2
        assert result[0].description == "Write unit tests"
        assert result[0].assignee_id is None
        assert result[1].description == "Update documentation"

    def test_with_assigned_to_syntax(self) -> None:
        text = "# Action Items\n- Implement feature (assigned to alice)\n"
        result = parse_action_items(text)
        assert len(result) == 1
        assert result[0].description == "Implement feature"
        assert result[0].assignee_id == "alice"

    def test_with_assignee_colon_syntax(self) -> None:
        text = "# Action Items\n- Review PR assignee: bob\n"
        result = parse_action_items(text)
        assert len(result) == 1
        assert result[0].description == "Review PR"
        assert result[0].assignee_id == "bob"

    def test_no_action_items_section(self) -> None:
        text = "Summary without action items."
        result = parse_action_items(text)
        assert result == ()

    def test_empty_string(self) -> None:
        result = parse_action_items("")
        assert result == ()

    def test_empty_items_skipped(self) -> None:
        text = "# Action Items\n1.   \n2. Real item\n"
        result = parse_action_items(text)
        assert len(result) == 1
        assert result[0].description == "Real item"

    def test_section_stops_at_next_header(self) -> None:
        text = "# Action Items\n- Do this\n# Notes\n- Not an action item\n"
        result = parse_action_items(text)
        assert len(result) == 1
        assert result[0].description == "Do this"

    def test_case_insensitive_header(self) -> None:
        text = "ACTION ITEMS:\n- Task one\n"
        result = parse_action_items(text)
        assert len(result) == 1


@pytest.mark.unit
class TestParseDecisionsAndActions:
    def test_both_sections_in_one_text(self) -> None:
        text = (
            "# Decisions\n"
            "1. Approve the design\n"
            "# Action Items\n"
            "1. Implement prototype (assigned to charlie)\n"
        )
        decisions = parse_decisions(text)
        actions = parse_action_items(text)
        assert decisions == ("Approve the design",)
        assert len(actions) == 1
        assert actions[0].assignee_id == "charlie"
