"""Shipped templates tune the protocol their ceremonies run on.

A ceremony's ``protocol_config`` is authorable YAML, so a typo in a builtin
should fail here rather than at company creation.
"""

from typing import ClassVar

import pytest

from synthorg.communication.meeting.config import (
    DEFAULT_CONFLICT_SIMILARITY_THRESHOLD,
)
from synthorg.communication.meeting.enums import (
    ConflictDetectorType,
    MeetingProtocolType,
)
from synthorg.engine.workflow.sprint_config import SprintCeremonyConfig
from synthorg.templates.loader import load_template
from synthorg.templates.renderer import render_template

pytestmark = pytest.mark.unit


def _ceremony(name: str, template: str) -> SprintCeremonyConfig:
    config = render_template(load_template(template))
    by_name = {c.name: c for c in config.workflow.sprint.ceremonies}
    return by_name[name]


class TestCeremonyProtocolConfigInBuiltins:
    """Two defaults fight their ceremony's own budget; those are tuned."""

    #: Templates whose sprint_planning is a structured-phases ceremony.
    _PLANNING_TEMPLATES: ClassVar[list[str]] = [
        "startup",
        "dev_shop",
        "product_team",
        "full_company",
    ]

    #: Templates that ship a daily standup.
    _STANDUP_TEMPLATES: ClassVar[list[str]] = ["dev_shop", "full_company"]

    @pytest.mark.parametrize("template", _PLANNING_TEMPLATES)
    def test_planning_raises_the_discussion_budget(self, template: str) -> None:
        """1000 flat tokens cannot use a 5000-token ceremony's budget."""
        planning = _ceremony("sprint_planning", template)
        sub_config = planning.protocol_config.structured_phases
        assert sub_config.max_discussion_tokens == 2000

    @pytest.mark.parametrize("template", _PLANNING_TEMPLATES)
    def test_planning_compares_positions_by_embedding(self, template: str) -> None:
        planning = _ceremony("sprint_planning", template)
        sub_config = planning.protocol_config.structured_phases
        assert sub_config.conflict_detector is ConflictDetectorType.EMBEDDING

    @pytest.mark.parametrize("template", _PLANNING_TEMPLATES)
    def test_planning_protocol_config_matches_the_ceremony_protocol(
        self,
        template: str,
    ) -> None:
        planning = _ceremony("sprint_planning", template)
        assert (
            planning.protocol_config.protocol is MeetingProtocolType.STRUCTURED_PHASES
        )

    @pytest.mark.parametrize("template", _STANDUP_TEMPLATES)
    def test_standup_is_one_turn_each(self, template: str) -> None:
        standup = _ceremony("daily_standup", template)
        assert standup.protocol_config.round_robin.max_turns_per_agent == 1

    @pytest.mark.parametrize("template", _PLANNING_TEMPLATES)
    def test_untuned_ceremonies_keep_the_shipped_defaults(
        self,
        template: str,
    ) -> None:
        """Everything not named above stays on defaults, deliberately.

        Including ``conflict_similarity_threshold``: the shipped default
        is already calibrated for the hashing embedder these templates'
        ``embedding`` detector is handed, so a per-template number would
        be a second, unmeasured answer to the same question.
        """
        planning = _ceremony("sprint_planning", template)
        sub_config = planning.protocol_config.structured_phases
        assert (
            sub_config.conflict_similarity_threshold
            == DEFAULT_CONFLICT_SIMILARITY_THRESHOLD
        )
        assert sub_config.skip_discussion_if_no_conflicts is True
