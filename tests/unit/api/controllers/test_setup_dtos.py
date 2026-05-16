"""Tests for setup wizard DTO validation.

Covers SetupAgentRequest, SetupCompanyRequest, SetupStatusResponse,
and SetupAgentSummary Pydantic model validation.
"""

import pytest
from pydantic import ValidationError


@pytest.mark.unit
class TestSetupDTOs:
    """Unit tests for setup DTO validation."""

    def test_setup_agent_request_valid_preset(self) -> None:
        from synthorg.api.controllers.setup_models import SetupAgentRequest

        req = SetupAgentRequest(
            name="Alice",
            role="CEO",
            personality_preset="Visionary_Leader",
            model_provider="test-provider",
            model_id="model-001",
        )
        # Validator normalizes to lowercase
        assert req.personality_preset == "visionary_leader"

    def test_setup_agent_request_invalid_preset(self) -> None:
        from synthorg.api.controllers.setup_models import SetupAgentRequest

        with pytest.raises(ValidationError, match="personality preset"):
            SetupAgentRequest(
                name="Alice",
                role="CEO",
                personality_preset="nonexistent",
                model_provider="test-provider",
                model_id="model-001",
            )

    def test_setup_company_request_defaults(self) -> None:
        from synthorg.api.controllers.setup_models import SetupCompanyRequest

        req = SetupCompanyRequest(company_name="Test Corp")
        assert req.template_name is None
        assert req.description is None

    def test_setup_status_response_frozen(self) -> None:
        from synthorg.api.controllers.setup_models import SetupStatusResponse

        resp = SetupStatusResponse(
            needs_admin=True,
            needs_setup=True,
            has_providers=False,
            has_name_locales=False,
            has_company=False,
            has_agents=False,
            min_password_length=12,
        )
        with pytest.raises(ValidationError):
            resp.needs_admin = False  # type: ignore[misc]

    def test_setup_complete_response_rejects_whitespace_failure_reason(
        self,
    ) -> None:
        from synthorg.api.controllers.setup_models import SetupCompleteResponse

        with pytest.raises(ValidationError):
            SetupCompleteResponse(
                setup_complete=True,
                embedder_selected=False,
                embedder_failure_reason="   ",
            )

    def test_setup_complete_response_accepts_real_failure_reason(self) -> None:
        from synthorg.api.controllers.setup_models import SetupCompleteResponse

        resp = SetupCompleteResponse(
            setup_complete=True,
            embedder_selected=False,
            embedder_failure_reason="no embedding-capable model available",
        )
        assert resp.embedder_failure_reason == ("no embedding-capable model available")

    @pytest.mark.parametrize(
        ("level", "model_provider", "model_id"),
        [
            (None, None, None),
            ("junior", "test-provider", "test-model-001"),
            ("senior", None, None),
        ],
    )
    def test_setup_agent_summary_nullable_fields(
        self,
        level: str | None,
        model_provider: str | None,
        model_id: str | None,
    ) -> None:
        """SetupAgentSummary accepts None for optional fields."""
        from synthorg.api.controllers.setup_models import SetupAgentSummary

        summary = SetupAgentSummary(
            name="Alice",
            role="Developer",
            department="Engineering",
            level=level,  # type: ignore[arg-type]
            model_provider=model_provider,
            model_id=model_id,
        )
        assert summary.level == level
        assert summary.model_provider == model_provider
        assert summary.model_id == model_id

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("level", ""),
            ("model_provider", ""),
            ("model_id", ""),
            ("model_provider", "   "),
        ],
    )
    def test_setup_agent_summary_rejects_blank_strings(
        self,
        field: str,
        value: str,
    ) -> None:
        """SetupAgentSummary rejects empty/blank strings for typed fields."""
        from synthorg.api.controllers.setup_models import SetupAgentSummary

        with pytest.raises(ValidationError):
            SetupAgentSummary(
                name="Alice",
                role="Developer",
                department="Engineering",
                **{field: value},  # type: ignore[arg-type]
            )


@pytest.mark.unit
class TestSetupDTOBeforeValidatorImmutability:
    """The personality-preset before-validators must not mutate caller input."""

    def test_setup_agent_request_does_not_mutate_input(self) -> None:
        import copy

        from synthorg.api.controllers.setup_models import SetupAgentRequest

        original = {
            "name": "Alice",
            "role": "CEO",
            "personality_preset": "Visionary_Leader",
            "model_provider": "test-provider",
            "model_id": "model-001",
        }
        snapshot = copy.deepcopy(original)
        req = SetupAgentRequest.model_validate(original)

        assert original == snapshot, "before-validator mutated caller input"
        assert req.personality_preset == "visionary_leader"

    def test_setup_agent_request_default_preset_does_not_mutate(self) -> None:
        import copy

        from synthorg.api.controllers.setup_models import SetupAgentRequest

        original: dict[str, object] = {
            "name": "Alice",
            "role": "CEO",
            "model_provider": "test-provider",
            "model_id": "model-001",
        }
        snapshot = copy.deepcopy(original)
        req = SetupAgentRequest.model_validate(original)

        assert original == snapshot, "before-validator added a key to caller input"
        assert req.personality_preset == "pragmatic_builder"

    def test_update_agent_personality_request_does_not_mutate_input(self) -> None:
        import copy

        from synthorg.api.controllers.setup_models import (
            UpdateAgentPersonalityRequest,
        )

        original = {"personality_preset": "Visionary_Leader"}
        snapshot = copy.deepcopy(original)
        req = UpdateAgentPersonalityRequest.model_validate(original)

        assert original == snapshot, "before-validator mutated caller input"
        assert req.personality_preset == "visionary_leader"
