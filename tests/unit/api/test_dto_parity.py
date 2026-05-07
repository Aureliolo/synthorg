"""Tests for DTO parity contracts.

Covers:
- ``UpdateDepartmentRequest.teams`` requires structured ``Team`` objects
- ``UpdateAgentOrgRequest`` gained ``model_provider`` / ``model_id`` with the
  pair-validation rule shared with ``CreateAgentOrgRequest``.
- ``PullModelRequest`` / ``UpdateModelConfigRequest`` accept the expected
  payloads and reject malformed input.
"""

import pytest
from pydantic import ValidationError

from synthorg.api.dto_org import (
    UpdateAgentOrgRequest,
    UpdateDepartmentRequest,
)
from synthorg.api.dto_providers import PullModelRequest, UpdateModelConfigRequest
from synthorg.config.schema import LocalModelParams
from synthorg.core.company import Team

pytestmark = pytest.mark.unit


class TestUpdateDepartmentRequestTeams:
    """`teams` is now a tuple of typed Team models, not raw dicts."""

    def test_accepts_team_model_instances(self) -> None:
        req = UpdateDepartmentRequest(
            teams=(
                Team(name="alpha", lead="lead-agent", members=("m1", "m2")),
                Team(name="beta", lead="lead-two"),
            ),
        )

        assert req.teams is not None
        assert len(req.teams) == 2
        assert req.teams[0].name == "alpha"
        assert req.teams[0].members == ("m1", "m2")
        assert req.teams[1].members == ()

    def test_accepts_dict_payload_by_coercion(self) -> None:
        """JSON decoded into dicts must still validate into Team objects."""
        req = UpdateDepartmentRequest.model_validate(
            {
                "teams": [
                    {"name": "alpha", "lead": "l1", "members": ["m1"]},
                ],
            },
        )

        assert req.teams is not None
        assert isinstance(req.teams[0], Team)
        assert req.teams[0].members == ("m1",)

    def test_rejects_team_missing_required_field(self) -> None:
        with pytest.raises(ValidationError):
            UpdateDepartmentRequest.model_validate(
                {"teams": [{"name": "alpha"}]},  # missing required `lead`
            )

    def test_rejects_team_with_blank_name(self) -> None:
        with pytest.raises(ValidationError):
            UpdateDepartmentRequest.model_validate(
                {"teams": [{"name": "   ", "lead": "l1"}]},
            )

    def test_teams_none_is_allowed(self) -> None:
        req = UpdateDepartmentRequest()

        assert req.teams is None

    def test_rejects_teams_beyond_max_length(self) -> None:
        oversize = tuple(
            Team(name=f"team-{i:03d}", lead=f"lead-{i}") for i in range(65)
        )
        with pytest.raises(ValidationError):
            UpdateDepartmentRequest(teams=oversize)


class TestUpdateAgentOrgRequestModel:
    """Added `model_provider` + `model_id` with paired validation."""

    def test_accepts_both_provider_and_id(self) -> None:
        req = UpdateAgentOrgRequest(
            model_provider="test-provider",
            model_id="test-medium-001",
        )

        assert req.model_provider == "test-provider"
        assert req.model_id == "test-medium-001"

    def test_accepts_neither_provider_nor_id(self) -> None:
        req = UpdateAgentOrgRequest(role="engineer")

        assert req.model_provider is None
        assert req.model_id is None

    def test_rejects_only_provider(self) -> None:
        with pytest.raises(ValidationError, match="model_provider and model_id"):
            UpdateAgentOrgRequest(model_provider="test-provider")

    def test_rejects_only_model_id(self) -> None:
        with pytest.raises(ValidationError, match="model_provider and model_id"):
            UpdateAgentOrgRequest(model_id="test-medium-001")


class TestPullModelRequest:
    """Allowed character set and length guardrails."""

    @pytest.mark.parametrize(
        "model_name",
        [
            "test-local-001",
            "test-local-001:latest",
            "vendor/model:v1.2",
            "model_name.with-dots@tag",
        ],
    )
    def test_accepts_valid_names(self, model_name: str) -> None:
        req = PullModelRequest(model_name=model_name)

        assert req.model_name == model_name

    def test_rejects_blank(self) -> None:
        with pytest.raises(ValidationError):
            PullModelRequest(model_name="   ")

    def test_rejects_illegal_chars(self) -> None:
        with pytest.raises(ValidationError):
            PullModelRequest(model_name="bad name!")


class TestUpdateModelConfigRequest:
    def test_accepts_local_params(self) -> None:
        params = LocalModelParams(num_ctx=4096, num_threads=8)
        req = UpdateModelConfigRequest(local_params=params)

        assert req.local_params.num_ctx == 4096
        assert req.local_params.num_threads == 8

    def test_rejects_missing_params(self) -> None:
        with pytest.raises(ValidationError):
            UpdateModelConfigRequest.model_validate({})
