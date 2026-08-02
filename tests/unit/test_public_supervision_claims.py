"""The supervision claims the README makes must stay true.

README.md's "How supervision works" section is the project's front door
and the most quotable statement of its security posture. Every other
public surface (docs/index.md, the marketing site, the comparison data)
repeats it. Prose is not covered by any gate: `check_doc_numeric_macros`
guards stat counts, `lychee` guards links, `vale` guards style, and none
of them notice when a claim stops being true.

So the load-bearing claims are pinned here against the code they
describe. A change that falsifies one breaks a test instead of quietly
turning the front page into a lie.
"""

from itertools import pairwise

import pytest

from synthorg.core.autonomy_enums import AutonomyLevel, compare_autonomy
from synthorg.settings import definitions as _settings_definitions  # noqa: F401
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

pytestmark = pytest.mark.unit


def _definition(namespace: SettingNamespace, key: str) -> SettingDefinition:
    definition = get_registry().get(namespace.value, key)
    assert definition is not None, f"{namespace.value}.{key} is not registered"
    return definition


class TestReadmeSupervisionClaims:
    """Pin the claims README.md's supervision section states as fact."""

    def test_four_oversight_modes_exist(self) -> None:
        """README: "Four modes, most oversight to least"."""
        assert set(AutonomyLevel) == {
            AutonomyLevel.LOCKED,
            AutonomyLevel.SUPERVISED,
            AutonomyLevel.SEMI,
            AutonomyLevel.FULL,
        }

    def test_modes_are_ordered_locked_to_full(self) -> None:
        """README lists them locked, supervised, semi, full in that order."""
        ladder = (
            AutonomyLevel.LOCKED,
            AutonomyLevel.SUPERVISED,
            AutonomyLevel.SEMI,
            AutonomyLevel.FULL,
        )
        for stricter, looser in pairwise(ladder):
            assert compare_autonomy(stricter, looser) < 0

    def test_completion_oracle_is_on_by_default(self) -> None:
        """README: "The completion oracle is on by default"."""
        definition = _definition(SettingNamespace.ENGINE, "completion_oracle_enabled")
        assert definition.default == "true"
