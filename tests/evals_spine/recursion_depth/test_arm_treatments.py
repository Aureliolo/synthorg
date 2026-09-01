# module-kind: tests
"""The sweep writes what every session remembers with, is watched by and is
compacted under, through the same live settings recursion is armed through."""

import json
from unittest.mock import AsyncMock

import pytest

from evals.recursion_depth.manifest import RecursionDepthManifest
from evals.recursion_depth.tree import arm_treatments
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def _manifest(**overrides: object) -> RecursionDepthManifest:
    payload: dict[str, object] = {
        "spec_dir": "evals/recursion_depth/spec/sqlcsv",
        "depths": [1],
        "repetitions": {1: 1},
        "arms": ["gated"],
        "executor": {
            "provider": "example-provider",
            "model_id": "example-capable-001",
            "capability": "capable",
            "family": "example-family-a",
        },
        "reviewer": {
            "provider": "example-provider",
            "model_id": "example-expert-001",
            "capability": "expert",
            "family": "example-family-b",
        },
        "independence": "cross_family",
        "embedder": {"provider": "embed-provider", "model_id": "example-embed-001"},
        "stagnation": {"strategy": "quality_erosion"},
        "compaction": {"fill_threshold_percent": 70.0, "summariser": None},
        "leaf_deep_claims": 4,
        "merge_attempts": 3,
        "unit_max_turns": 40,
        "planner_max_turns": 40,
        "unit_cost_ceiling": 2.0,
        "unit_token_ceiling": 600_000,
        "unit_token_per_claim": 0,
        "unit_token_cap": 4_000_000,
        "contract_stage": True,
        "contract_max_turns": 60,
        "contract_token_ceiling": 2_500_000,
        "merge_token_base": 600_000,
        "merge_token_per_piece": 100_000,
        "merge_token_cap": 2_000_000,
        "merge_max_turns_base": 40,
        "merge_max_turns_per_piece": 5,
        "merge_max_turns_cap": 120,
        "review_token_base": 600_000,
        "review_token_per_piece": 100_000,
        "review_token_cap": 2_000_000,
        "review_max_turns_base": 30,
        "review_max_turns_per_piece": 5,
        "review_max_turns_cap": 100,
        "max_sessions": 100,
        "projected_branching": 4,
        "expected_sessions_per_cell": {1: 20},
    }
    payload.update(overrides)
    return RecursionDepthManifest.model_validate(payload)


async def _writes(manifest: RecursionDepthManifest) -> dict[str, str]:
    settings = mock_of[SettingsService](
        set=AsyncMock(return_value=None), registry=get_registry()
    )
    await arm_treatments(settings, manifest)
    return {
        f"{call.args[0]}.{call.args[1]}": call.args[2]
        for call in settings.set.await_args_list
    }


class TestEveryTreatmentReachesTheLiveSettings:
    async def test_the_embedder_is_written_as_a_bound_model_ref(self) -> None:
        written = await _writes(_manifest())
        assert json.loads(written["memory.embedder_model"]) == {
            "provider": "embed-provider",
            "model_id": "example-embed-001",
        }

    async def test_the_detector_and_the_threshold_are_written(self) -> None:
        written = await _writes(_manifest())
        assert written["engine.stagnation_strategy"] == "quality_erosion"
        assert written["engine.compaction_fill_threshold_percent"] == "70.0"

    async def test_no_summariser_leaves_the_text_summary_in_force(self) -> None:
        written = await _writes(_manifest())
        assert written["engine.compaction_llm_summarizer_enabled"] == "false"
        assert written["engine.compaction_summary_model"] == ""

    async def test_a_summariser_pair_turns_the_semantic_summary_on(self) -> None:
        written = await _writes(
            _manifest(
                compaction={
                    "fill_threshold_percent": 80.0,
                    "summariser": {
                        "provider": "example-provider",
                        "model_id": "example-basic-001",
                        "capability": "basic",
                    },
                }
            )
        )
        assert written["engine.compaction_llm_summarizer_enabled"] == "true"
        assert json.loads(written["engine.compaction_summary_model"]) == {
            "provider": "example-provider",
            "model_id": "example-basic-001",
        }

    async def test_every_key_written_is_a_registered_setting(self) -> None:
        # A write the registry does not know is one the service would refuse
        # partway through a paid sweep; the double accepts anything, so the
        # registry is asked here instead.
        registry = get_registry()
        for qualified in await _writes(_manifest()):
            namespace, key = qualified.split(".", 1)
            assert registry.get(namespace, key) is not None, qualified
