"""Probe completion records cost via the chokepoint.

The connection-test endpoint sends a real "ping" completion to validate
provider connectivity. Every paid LLM call must emit a CostRecord, so
the probe is wrapped in :func:`cost_recording_scope`. This module is the
regression guard for that contract.
"""

from unittest.mock import patch

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
            autospec=True,
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
        await drain_pending_cost_records()
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
        await drain_pending_cost_records()
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
        await drain_pending_cost_records()
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


@pytest.mark.unit
class TestSafeTaskIdSegment:
    """Direct unit tests for ``_safe_task_id_segment``.

    The end-to-end path (provider creation -> probe -> task_id stamp)
    can't exercise this sanitiser because ``CreateProviderRequest``'s
    name validator rejects colons / control characters / whitespace
    long before they reach the probe. The sanitiser exists as
    defence-in-depth: if the validator regex is ever loosened, or if a
    future caller bypasses ``CreateProviderRequest`` (a different
    persistence path, a YAML-loaded provider, an admin-tool override),
    the sanitiser still keeps task-id segments well-formed. These
    direct tests pin that contract.
    """

    def test_colon_is_rewritten(self) -> None:
        """Colons must be replaced; they're the task-id segment delimiter."""
        from synthorg.providers.management.service import _safe_task_id_segment

        assert _safe_task_id_segment("bad:provider") == "bad_provider"

    def test_newline_and_other_controls_are_rewritten(self) -> None:
        """Control characters (newline / tab / carriage return / NUL)
        must be replaced so they can't break log line framing."""
        from synthorg.providers.management.service import _safe_task_id_segment

        assert _safe_task_id_segment("a\nb") == "a_b"
        assert _safe_task_id_segment("a\tb") == "a_b"
        assert _safe_task_id_segment("a\rb") == "a_b"
        assert _safe_task_id_segment("a\x00b") == "a_b"

    def test_whitespace_is_rewritten(self) -> None:
        from synthorg.providers.management.service import _safe_task_id_segment

        assert _safe_task_id_segment("a b") == "a_b"

    def test_combined_unsafe_chars_all_rewritten(self) -> None:
        """A single name carrying multiple unsafe characters has every
        one of them replaced -- the most likely real-world abuse path."""
        from synthorg.providers.management.service import _safe_task_id_segment

        assert _safe_task_id_segment("bad:provider\nname") == "bad_provider_name"

    def test_safe_unicode_preserved(self) -> None:
        """Printable non-ASCII characters survive the sanitiser
        unchanged -- the sanitiser is a security guard, not an
        ASCII-only filter."""
        from synthorg.providers.management.service import _safe_task_id_segment

        assert _safe_task_id_segment("provider-é") == "provider-é"
        assert _safe_task_id_segment("provider-日本") == "provider-日本"

    def test_all_unsafe_input_returns_underscore(self) -> None:
        """If every character is filtered, the helper returns ``"_"``
        so callers wrapping the result in ``NotBlankStr`` don't trip
        the empty-string guard."""
        from synthorg.providers.management.service import _safe_task_id_segment

        assert _safe_task_id_segment("\n\t\r") == "___"
        assert _safe_task_id_segment(":::") == "___"
