# module-kind: tests
"""The backend half of the criterion-key parity fixture.

``coverageKey`` in the dashboard is a second implementation of
:func:`~synthorg.core.criterion_match.criterion_key`, and nothing but this
fixture ties them together. The sibling test is
``web/src/__tests__/utils/criterionKeyParity.test.ts``; both key every case in
``data/criterion_key_cases.json``, so a change to either implementation fails
here or there rather than quietly placing a claim the other side refuses.
"""

import json
from pathlib import Path

import pytest

from synthorg.core.criterion_match import criterion_key

pytestmark = pytest.mark.unit

_FIXTURE = Path(__file__).resolve().parents[3] / "data" / "criterion_key_cases.json"


def _cases() -> list[tuple[str, str]]:
    """Read the shared fixture.

    Returns:
        Each case as its input text and the key both sides must produce.
    """
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    return [(case["text"], case["key"]) for case in payload["cases"]]


class TestTheSharedFixture:
    def test_the_fixture_is_present(self) -> None:
        """A missing fixture would pass an empty parametrize silently."""
        assert _FIXTURE.exists()

    def test_the_fixture_carries_cases(self) -> None:
        assert len(_cases()) >= 8


@pytest.mark.parametrize(("text", "expected"), _cases())
def test_every_case_keys_the_way_the_dashboard_must(text: str, expected: str) -> None:
    assert criterion_key(text) == expected
