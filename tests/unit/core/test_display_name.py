"""Tests for the one rule on what may appear in a name field."""

from uuid import uuid4

import pytest

from synthorg.core.display_name import display_name_or_none

pytestmark = pytest.mark.unit


class TestDisplayNameOrNone:
    def test_uuid_is_never_a_name(self) -> None:
        assert display_name_or_none(str(uuid4())) is None

    def test_uuid_without_dashes_is_never_a_name(self) -> None:
        # The wire form is canonical, but a hex spelling parses as the same
        # key, so accepting it as a name would leave the rule bypassable.
        assert display_name_or_none(uuid4().hex) is None

    @pytest.mark.parametrize(
        "value",
        [
            "plan_review_gate",
            "coordinator",
            "a2a-gateway:partner-org",
            "Aurelio",
        ],
    )
    def test_a_word_a_person_reads_survives(self, value: str) -> None:
        assert display_name_or_none(value) == value

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_nothing_is_not_a_name(self, value: str | None) -> None:
        assert display_name_or_none(value) is None
