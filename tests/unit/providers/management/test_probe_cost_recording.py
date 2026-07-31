"""Probe completion records cost via the chokepoint.

The connection-test endpoint sends a real "ping" completion to validate
provider connectivity. Every paid LLM call must emit a CostRecord, so
the probe is wrapped in :func:`cost_recording_scope`. This module is the
regression guard for that contract.
"""

from unittest.mock import patch

import pytest

from synthorg.api.dto_providers import TestConnectionRequest as ConnTestRequest
from synthorg.api.state import AppState
from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.config import BudgetConfig
from synthorg.budget.tracker import CostTracker
from synthorg.core.completion_enums import FinishReason
from synthorg.core.types import NotBlankStr
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.providers.drivers.litellm_driver import LiteLLMDriver
from synthorg.providers.management.service import ProviderManagementService
from synthorg.providers.models import CompletionResponse, TokenUsage
from synthorg.settings.state import config_resolver_of, settings_service_of

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
        settings_service=settings_service_of(app_state),
        config_resolver=config_resolver_of(app_state),
        app_state=app_state,
        config=app_state.config,
        backend_port=3001,
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
            autospec=True,
            return_value=_build_completion_response(),
        ):
            result = await service.test_connection(
                "test-provider",
                ConnTestRequest(),
            )

        assert result.success is True
        await tracker.drain_pending_records()

        records = await tracker.get_records()
        assert len(records) == 1
        record = records[0]
        # A connection probe belongs to no agent and no task. Naming one
        # anyway pointed task_id at a row that does not exist, so the
        # foreign key rejected the insert and the spend was never recorded.
        assert record.agent_id is None
        assert record.task_id is None
        assert record.call_category == LLMCallCategory.SYSTEM
        # What the call was is still recorded, which is why no id was needed.
        assert record.prompt_class_id == PromptPurposeId.PROVIDERS_TEST_CONNECTION
        assert record.provider == "test-provider"
        assert record.model == "test-model-001"
        assert record.currency == "USD"

    async def test_probe_with_no_tracker_is_noop(
        self,
        app_state: AppState,
    ) -> None:
        """Without a tracker the scope is a no-op AND leaves no
        cost-recording context active in the calling task.

        The two assertions cover both halves of the no-op contract:
        the call succeeds (no exception), and the chokepoint context
        var is unchanged (no leaked scope across boundaries)."""
        from synthorg.providers.cost_recording import current_cost_context

        service = _make_service_with_tracker(app_state, cost_tracker=None)
        await service.create_provider(make_create_request())

        assert current_cost_context() is None
        with patch.object(
            LiteLLMDriver,
            "_do_complete",
            autospec=True,
            return_value=_build_completion_response(),
        ):
            result = await service.test_connection(
                "test-provider",
                ConnTestRequest(),
            )

        assert result.success is True
        # No tracker passed -> no scope opened with one -> nothing to drain.
        # Scope tore down cleanly: no leaked context in the caller.
        assert current_cost_context() is None

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
            autospec=True,
            side_effect=AuthenticationError("Invalid key"),
        ):
            result = await service.test_connection(
                "test-provider",
                ConnTestRequest(),
            )

        assert result.success is False
        await tracker.drain_pending_records()
        records = await tracker.get_records()
        assert records == ()

    async def test_probe_retry_exhausted_records_signal(
        self,
        app_state: AppState,
    ) -> None:
        """A ``RetryExhaustedError`` raised inside the probe surfaces
        through ``_do_test_connection``'s explicit ``except`` clause
        and tags ``retry_exhausted=True`` on the warning event. Locks
        the contract so the retry-exhaustion signal isn't silently
        merged into the generic ProviderError path."""
        import structlog

        from synthorg.providers.errors import ProviderTimeoutError
        from synthorg.providers.resilience.errors import RetryExhaustedError

        tracker = CostTracker(
            budget_config=BudgetConfig(currency="USD"),
        )
        service = _make_service_with_tracker(app_state, tracker)
        await service.create_provider(make_create_request())

        # RetryExhaustedError wraps the last retryable provider error
        # the RetryHandler tried and gave up on.
        with (
            patch.object(
                LiteLLMDriver,
                "_do_complete",
                autospec=True,
                side_effect=RetryExhaustedError(
                    ProviderTimeoutError("upstream timed out"),
                ),
            ),
            structlog.testing.capture_logs() as events,
        ):
            result = await service.test_connection(
                "test-provider",
                ConnTestRequest(),
            )

        assert result.success is False
        assert result.error is not None
        # No CostRecord: the exception bypasses the chokepoint.
        await tracker.drain_pending_records()
        assert await tracker.get_records() == ()

        # Verify the WARNING fired with retry_exhausted=True. structlog
        # captures the event_dict directly; assert on the structured
        # field rather than substring-matching the rendered output.
        retry_events = [e for e in events if e.get("retry_exhausted") is True]
        assert retry_events, (
            f"expected at least one event carrying retry_exhausted=True; got: {events}"
        )

    async def test_probe_propagates_provider_exception(
        self,
        app_state: AppState,
    ) -> None:
        """An ``AuthenticationError`` raised inside the cost-recording
        scope propagates out of ``_probe_provider`` to the caller's
        exception handler. Without this, a future change that swallows
        exceptions in ``cost_recording_scope.__aexit__`` would silently
        report success on a failed probe."""
        from synthorg.providers.errors import AuthenticationError

        tracker = CostTracker(
            budget_config=BudgetConfig(currency="USD"),
        )
        service = _make_service_with_tracker(app_state, tracker)
        await service.create_provider(make_create_request())

        # Bypass _do_test_connection's catch by calling _probe_provider
        # directly: this asserts the scope's exception path, not the
        # handler's recovery path.
        from synthorg.providers.management.service import (
            ProviderManagementService,
        )

        providers = await service.list_providers()
        config = providers["test-provider"]
        with (
            patch.object(
                LiteLLMDriver,
                "_do_complete",
                autospec=True,
                side_effect=AuthenticationError("Invalid key"),
            ),
            pytest.raises(AuthenticationError, match="Invalid key"),
        ):
            await ProviderManagementService._probe_provider(
                service,
                "test-provider",
                config,
                "test-model-001",
            )
