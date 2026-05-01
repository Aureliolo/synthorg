"""Probe completion records cost via the chokepoint.

The connection-test endpoint sends a real "ping" completion to validate
provider connectivity. Every paid LLM call must emit a CostRecord, so
the probe is wrapped in :func:`cost_recording_scope`. This module is the
regression guard for that contract.
"""

from unittest.mock import AsyncMock, patch

import pytest

from synthorg.api.dto import TestConnectionRequest as ConnTestRequest
from synthorg.api.state import AppState
from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.config import BudgetConfig
from synthorg.budget.tracker import CostTracker
from synthorg.core.types import NotBlankStr
from synthorg.providers.cost_recording import drain_pending_cost_records
from synthorg.providers.drivers.litellm_driver import LiteLLMDriver
from synthorg.providers.enums import FinishReason
from synthorg.providers.management.service import ProviderManagementService
from synthorg.providers.models import CompletionResponse, TokenUsage

from .conftest import make_create_request


def _build_completion_response() -> CompletionResponse:
    """Build a typed CompletionResponse with non-zero usage so the
    chokepoint records (zero-cost AND zero-token responses are skipped)."""
    return CompletionResponse(
        content="pong",
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=1, output_tokens=1, cost=0.0),
        model=NotBlankStr("test-model-001"),
    )


def _make_service_with_tracker(
    app_state: AppState,
    cost_tracker: CostTracker | None,
) -> ProviderManagementService:
    """ProviderManagementService that mirrors the conftest ``service``
    fixture but threads a CostTracker through the constructor."""
    return ProviderManagementService(
        settings_service=app_state.settings_service,
        config_resolver=app_state.config_resolver,
        app_state=app_state,
        config=app_state.config,
        cost_tracker=cost_tracker,
    )


@pytest.mark.unit
class TestProbeCostRecording:
    """`_probe_provider` records its completion via the chokepoint."""

    async def test_probe_emits_one_cost_record(
        self,
        app_state: AppState,
    ) -> None:
        """A successful probe lands exactly one CostRecord."""
        tracker = CostTracker(
            budget_config=BudgetConfig(currency="USD"),
        )
        service = _make_service_with_tracker(app_state, tracker)
        await service.create_provider(make_create_request())

        with patch.object(
            LiteLLMDriver,
            "_do_complete",
            new_callable=AsyncMock,
            return_value=_build_completion_response(),
        ):
            result = await service.test_connection(
                "test-provider",
                ConnTestRequest(),
            )

        assert result.success is True
        await drain_pending_cost_records()

        records = await tracker.get_records()
        assert len(records) == 1
        record = records[0]
        assert record.agent_id == "system"
        assert record.task_id == "system:providers:test_connection:test-provider"
        assert record.call_category == LLMCallCategory.SYSTEM
        assert record.provider == "test-provider"
        assert record.model == "test-model-001"
        assert record.currency == "USD"

    async def test_probe_with_no_tracker_is_noop(
        self,
        app_state: AppState,
    ) -> None:
        """Without a tracker the scope is a no-op; no record is emitted."""
        service = _make_service_with_tracker(app_state, cost_tracker=None)
        await service.create_provider(make_create_request())

        with patch.object(
            LiteLLMDriver,
            "_do_complete",
            new_callable=AsyncMock,
            return_value=_build_completion_response(),
        ):
            result = await service.test_connection(
                "test-provider",
                ConnTestRequest(),
            )

        assert result.success is True
        await drain_pending_cost_records()
        # No tracker wired -> nothing to assert beyond the absence of
        # an exception. The chokepoint reads ``None`` from the context
        # and is a true no-op.

    async def test_probe_failure_does_not_record(
        self,
        app_state: AppState,
    ) -> None:
        """Failed probes do not emit a CostRecord (no completion happened)."""
        from synthorg.providers.errors import AuthenticationError

        tracker = CostTracker(
            budget_config=BudgetConfig(currency="USD"),
        )
        service = _make_service_with_tracker(app_state, tracker)
        await service.create_provider(make_create_request())

        with patch.object(
            LiteLLMDriver,
            "_do_complete",
            new_callable=AsyncMock,
            side_effect=AuthenticationError("Invalid key"),
        ):
            result = await service.test_connection(
                "test-provider",
                ConnTestRequest(),
            )

        assert result.success is False
        await drain_pending_cost_records()
        records = await tracker.get_records()
        assert records == ()
