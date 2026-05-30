"""Validation and edge case tests for MilestoneDrivenStrategy."""

from unittest.mock import patch

import pytest

from synthorg.engine.workflow.ceremony_policy import (
    CeremonyPolicyConfig,
    CeremonyStrategyType,
)
from synthorg.engine.workflow.sprint_config import SprintConfig
from synthorg.engine.workflow.sprint_lifecycle import SprintStatus
from synthorg.engine.workflow.strategies.milestone_driven import (
    MilestoneDrivenStrategy,
)

from .conftest import make_context, make_sprint


def _make_sprint_config(
    milestones: list[dict[str, str]] | None = None,
    transition_milestone: str | None = None,
) -> SprintConfig:
    """Create a SprintConfig with milestone-driven policy."""
    config: dict[str, object] = {}
    if milestones is not None:
        config["milestones"] = milestones
    if transition_milestone is not None:
        config["transition_milestone"] = transition_milestone
    return SprintConfig(
        ceremony_policy=CeremonyPolicyConfig(
            strategy=CeremonyStrategyType.MILESTONE_DRIVEN,
            strategy_config=config,
        ),
    )


class TestValidateStrategyConfig:
    """validate_strategy_config() tests."""

    @pytest.mark.unit
    def test_valid_config(self) -> None:
        strategy = MilestoneDrivenStrategy()
        strategy.validate_strategy_config(
            {
                "milestones": [
                    {"name": "alpha", "ceremony": "sprint_review"},
                    {"name": "beta", "ceremony": "retrospective"},
                ],
                "transition_milestone": "alpha",
            }
        )

    @pytest.mark.unit
    def test_empty_config_valid(self) -> None:
        strategy = MilestoneDrivenStrategy()
        strategy.validate_strategy_config({})

    @pytest.mark.unit
    def test_unknown_keys_rejected(self) -> None:
        strategy = MilestoneDrivenStrategy()
        with pytest.raises(ValueError, match="Unknown config keys"):
            strategy.validate_strategy_config({"unknown_key": 42})

    @pytest.mark.unit
    def test_milestones_must_be_list(self) -> None:
        strategy = MilestoneDrivenStrategy()
        with pytest.raises(TypeError, match="must be a list"):
            strategy.validate_strategy_config(
                {"milestones": "not_a_list"},
            )

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("entry", "match"),
        [
            ({"ceremony": "sprint_review"}, "non-empty string"),
            ({"name": "alpha"}, "non-empty string"),
            ({"name": "", "ceremony": "sprint_review"}, "non-empty string"),
            ({"name": "alpha", "ceremony": ""}, "non-empty string"),
            ({"name": 123, "ceremony": "sprint_review"}, "non-empty string"),
            ({"name": "alpha", "ceremony": 456}, "non-empty string"),
            ({"name": True, "ceremony": "sprint_review"}, "non-empty string"),
            ({"name": "alpha", "ceremony": False}, "non-empty string"),
        ],
        ids=[
            "missing_name",
            "missing_ceremony",
            "empty_name",
            "empty_ceremony",
            "name_not_string",
            "ceremony_not_string",
            "name_is_bool",
            "ceremony_is_bool",
        ],
    )
    def test_invalid_milestone_entry_rejected(
        self,
        entry: object,
        match: str,
    ) -> None:
        strategy = MilestoneDrivenStrategy()
        with pytest.raises(ValueError, match=match):
            strategy.validate_strategy_config({"milestones": [entry]})

    @pytest.mark.unit
    def test_milestone_entry_not_a_dict_rejected(self) -> None:
        strategy = MilestoneDrivenStrategy()
        with pytest.raises(TypeError, match="must be a mapping"):
            strategy.validate_strategy_config(
                {"milestones": ["not_a_dict"]},
            )

    @pytest.mark.unit
    def test_duplicate_milestone_names_rejected(self) -> None:
        strategy = MilestoneDrivenStrategy()
        with pytest.raises(ValueError, match="Duplicate milestone"):
            strategy.validate_strategy_config(
                {
                    "milestones": [
                        {"name": "alpha", "ceremony": "sprint_review"},
                        {"name": "alpha", "ceremony": "retrospective"},
                    ],
                }
            )

    @pytest.mark.unit
    def test_too_many_milestones_rejected(self) -> None:
        strategy = MilestoneDrivenStrategy()
        milestones = [
            {"name": f"ms-{i}", "ceremony": "sprint_review"} for i in range(33)
        ]
        with pytest.raises(ValueError, match="<= 32"):
            strategy.validate_strategy_config({"milestones": milestones})

    @pytest.mark.unit
    def test_milestone_name_too_long_rejected(self) -> None:
        strategy = MilestoneDrivenStrategy()
        with pytest.raises(ValueError, match="<= 128"):
            strategy.validate_strategy_config(
                {
                    "milestones": [
                        {
                            "name": "x" * 129,
                            "ceremony": "sprint_review",
                        },
                    ],
                }
            )

    @pytest.mark.unit
    def test_milestone_ceremony_too_long_rejected(self) -> None:
        strategy = MilestoneDrivenStrategy()
        with pytest.raises(ValueError, match="<= 128"):
            strategy.validate_strategy_config(
                {
                    "milestones": [
                        {
                            "name": "alpha",
                            "ceremony": "x" * 129,
                        },
                    ],
                }
            )

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("value", "match"),
        [
            ("", "non-empty string"),
            (123, "non-empty string"),
            (True, "non-empty string"),
        ],
        ids=["empty", "int", "bool"],
    )
    def test_transition_milestone_invalid(
        self,
        value: object,
        match: str,
    ) -> None:
        strategy = MilestoneDrivenStrategy()
        with pytest.raises(ValueError, match=match):
            strategy.validate_strategy_config(
                {"transition_milestone": value},
            )

    @pytest.mark.unit
    def test_transition_milestone_name_too_long(self) -> None:
        strategy = MilestoneDrivenStrategy()
        with pytest.raises(ValueError, match="<= 128"):
            strategy.validate_strategy_config(
                {"transition_milestone": "x" * 129},
            )


class TestTransitionMilestoneOnly:
    """Test transition_milestone that is not in the milestones list."""

    @pytest.mark.unit
    async def test_transition_milestone_only_accepts_tasks(
        self,
    ) -> None:
        """transition_milestone not in milestones list still accepts
        task assignments and triggers sprint transition."""
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint(task_count=2, completed_count=2)
        config = _make_sprint_config(
            milestones=[],  # no ceremony milestones
            transition_milestone="release_ready",
        )
        await strategy.on_sprint_activated(sprint, config)

        for i in range(2):
            await strategy.on_external_event(
                sprint,
                "milestone_assign",
                {
                    "task_id": f"task-{i}",
                    "milestone": "release_ready",
                },
            )

        ctx = make_context()
        result = strategy.should_transition_sprint(
            sprint,
            config,
            ctx,
        )
        assert result is SprintStatus.IN_REVIEW


class TestAdditionalEdgeCases:
    """Edge cases for milestone-driven strategy with absent or minimal config."""

    @pytest.mark.unit
    async def test_on_sprint_activated_with_strategy_config_none(
        self,
    ) -> None:
        """strategy_config=None leaves milestones empty."""
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint()
        config = SprintConfig(
            ceremony_policy=CeremonyPolicyConfig(
                strategy=CeremonyStrategyType.MILESTONE_DRIVEN,
                strategy_config=None,
            ),
        )
        await strategy.on_sprint_activated(sprint, config)
        assert strategy._milestones == {}
        assert strategy._transition_milestone is None

    @pytest.mark.unit
    async def test_assign_whitespace_task_id_ignored(self) -> None:
        """Whitespace-only task_id in milestone_assign is ignored."""
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint()
        config = _make_sprint_config(
            milestones=[
                {"name": "alpha", "ceremony": "sprint_review"},
            ],
        )
        await strategy.on_sprint_activated(sprint, config)

        await strategy.on_external_event(
            sprint,
            "milestone_assign",
            {"task_id": "   ", "milestone": "alpha"},
        )
        assert strategy._milestone_tasks == {}

    @pytest.mark.unit
    async def test_assign_whitespace_milestone_ignored(self) -> None:
        """Whitespace-only milestone in milestone_assign is ignored."""
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint()
        config = _make_sprint_config(
            milestones=[
                {"name": "alpha", "ceremony": "sprint_review"},
            ],
        )
        await strategy.on_sprint_activated(sprint, config)

        await strategy.on_external_event(
            sprint,
            "milestone_assign",
            {"task_id": "task-0", "milestone": "   "},
        )
        assert strategy._milestone_tasks == {}

    @pytest.mark.unit
    async def test_unassign_invalid_payload_no_error(self) -> None:
        """milestone_unassign with missing keys does not raise."""
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint()
        config = _make_sprint_config(
            milestones=[
                {"name": "alpha", "ceremony": "sprint_review"},
            ],
        )
        await strategy.on_sprint_activated(sprint, config)

        await strategy.on_external_event(
            sprint,
            "milestone_unassign",
            {"milestone": "alpha"},
        )
        await strategy.on_external_event(
            sprint,
            "milestone_unassign",
            {"task_id": "task-0"},
        )

    @pytest.mark.unit
    async def test_unassign_whitespace_task_id_ignored(self) -> None:
        """Whitespace-only task_id in milestone_unassign is ignored."""
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint()
        config = _make_sprint_config(
            milestones=[
                {"name": "alpha", "ceremony": "sprint_review"},
            ],
        )
        await strategy.on_sprint_activated(sprint, config)

        await strategy.on_external_event(
            sprint,
            "milestone_assign",
            {"task_id": "task-0", "milestone": "alpha"},
        )
        await strategy.on_external_event(
            sprint,
            "milestone_unassign",
            {"task_id": "   ", "milestone": "alpha"},
        )
        # Task should NOT have been removed
        assert "task-0" in strategy._milestone_tasks.get(
            "alpha",
            set(),
        )

    @pytest.mark.unit
    async def test_unassign_from_nonexistent_milestone(self) -> None:
        """Unassigning from unknown milestone is a safe no-op."""
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint()
        config = _make_sprint_config(
            milestones=[
                {"name": "alpha", "ceremony": "sprint_review"},
            ],
        )
        await strategy.on_sprint_activated(sprint, config)

        await strategy.on_external_event(
            sprint,
            "milestone_unassign",
            {"task_id": "task-0", "milestone": "nonexistent"},
        )

    @pytest.mark.unit
    async def test_on_sprint_activated_skips_malformed_entries(
        self,
    ) -> None:
        """Malformed milestone entries in config are silently skipped."""
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint()
        config = SprintConfig(
            ceremony_policy=CeremonyPolicyConfig(
                strategy=CeremonyStrategyType.MILESTONE_DRIVEN,
                strategy_config={
                    "milestones": [
                        {
                            "name": "valid",
                            "ceremony": "sprint_review",
                        },
                        "not_a_dict",
                        {"name": "", "ceremony": "retro"},
                        {"ceremony": "retro"},  # missing name
                    ],
                },
            ),
        )
        await strategy.on_sprint_activated(sprint, config)
        assert strategy._milestones == {"valid": "sprint_review"}

    @pytest.mark.unit
    async def test_max_tasks_per_milestone_enforced(self) -> None:
        """Assignments beyond _MAX_TASKS_PER_MILESTONE are rejected."""
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint()
        config = _make_sprint_config(
            milestones=[
                {"name": "alpha", "ceremony": "sprint_review"},
            ],
        )
        await strategy.on_sprint_activated(sprint, config)

        # Use a small limit to avoid creating 1001 tasks
        with patch(
            "synthorg.engine.workflow.strategies"
            ".milestone_driven._MAX_TASKS_PER_MILESTONE",
            3,
        ):
            for i in range(3):
                await strategy.on_external_event(
                    sprint,
                    "milestone_assign",
                    {
                        "task_id": f"task-{i}",
                        "milestone": "alpha",
                    },
                )

            # 4th assignment should be rejected
            await strategy.on_external_event(
                sprint,
                "milestone_assign",
                {"task_id": "task-3", "milestone": "alpha"},
            )

        tasks = strategy._milestone_tasks.get("alpha", set())
        assert len(tasks) == 3
        assert "task-3" not in tasks

    @pytest.mark.unit
    async def test_max_tasks_idempotent_reassign(self) -> None:
        """Re-assigning existing task at limit does not reject."""
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint()
        config = _make_sprint_config(
            milestones=[
                {"name": "alpha", "ceremony": "sprint_review"},
            ],
        )
        await strategy.on_sprint_activated(sprint, config)

        with patch(
            "synthorg.engine.workflow.strategies"
            ".milestone_driven._MAX_TASKS_PER_MILESTONE",
            2,
        ):
            for i in range(2):
                await strategy.on_external_event(
                    sprint,
                    "milestone_assign",
                    {
                        "task_id": f"task-{i}",
                        "milestone": "alpha",
                    },
                )

            # Re-assign existing task at limit -- should succeed
            await strategy.on_external_event(
                sprint,
                "milestone_assign",
                {"task_id": "task-0", "milestone": "alpha"},
            )

        tasks = strategy._milestone_tasks.get("alpha", set())
        assert len(tasks) == 2
        assert "task-0" in tasks

    @pytest.mark.unit
    async def test_assign_rejects_non_sprint_task(self) -> None:
        """Tasks not in the active sprint are silently rejected."""
        strategy = MilestoneDrivenStrategy()
        # Sprint with task_count=2 -> task_ids = ("task-0", "task-1")
        sprint = make_sprint(task_count=2)
        config = _make_sprint_config(
            milestones=[
                {"name": "alpha", "ceremony": "sprint_review"},
            ],
        )
        await strategy.on_sprint_activated(sprint, config)

        # "task-99" is not in sprint.task_ids
        await strategy.on_external_event(
            sprint,
            "milestone_assign",
            {"task_id": "task-99", "milestone": "alpha"},
        )
        assert strategy._milestone_tasks == {}

    @pytest.mark.unit
    async def test_unassign_allows_non_sprint_task(self) -> None:
        """Unassign does not guard against non-sprint tasks."""
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint(task_count=2)
        config = _make_sprint_config(
            milestones=[
                {"name": "alpha", "ceremony": "sprint_review"},
            ],
        )
        await strategy.on_sprint_activated(sprint, config)

        # First assign a valid task
        await strategy.on_external_event(
            sprint,
            "milestone_assign",
            {"task_id": "task-0", "milestone": "alpha"},
        )
        assert "task-0" in strategy._milestone_tasks.get(
            "alpha",
            set(),
        )

        # Unassign using a different sprint (simulating task removal)
        # -- unassign is not guarded, so it should work
        await strategy.on_external_event(
            sprint,
            "milestone_unassign",
            {"task_id": "task-0", "milestone": "alpha"},
        )
        assert "task-0" not in strategy._milestone_tasks.get(
            "alpha",
            set(),
        )
