"""Shared fixtures and helpers for setup controller tests."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from litestar.testing import TestClient


def _build_mock_provider_management() -> MagicMock:
    """Build a stub ``ProviderManagementService`` with one priced model."""
    mock_model = MagicMock()
    mock_model.id = "test-small-001"
    mock_model.alias = None
    mock_model.cost_per_1k_input = 0.01
    mock_model.cost_per_1k_output = 0.02
    mock_model.max_context = 200_000
    mock_model.estimated_latency_ms = 100
    mock_provider_config = MagicMock()
    mock_provider_config.models = (mock_model,)

    mock_mgmt = MagicMock()
    mock_mgmt.list_providers = AsyncMock(
        return_value={"test-provider": mock_provider_config},
    )
    return mock_mgmt


@contextmanager
def mock_providers(test_client: TestClient[Any]) -> Iterator[Any]:
    """Patch ``app_state._provider_management`` with a stub for the test body.

    The previous helper returned ``(app_state, original)`` and required
    every caller to wrap the body in their own ``try/finally``. A
    contextmanager makes restoration unconditional even when an
    intermediate ``raise`` skips the manual finally block, fixing the
    fragile state-restoration pattern flagged in the pre-PR review for
    issue #1666.
    """
    app_state = test_client.app.state.app_state
    original = app_state._provider_management
    app_state._provider_management = _build_mock_provider_management()
    try:
        yield app_state
    finally:
        app_state._provider_management = original


def setup_mock_providers(
    test_client: TestClient[Any],
) -> tuple[Any, Any]:
    """Patch ``app_state._provider_management`` with a stub.

    Legacy entry point kept for tests that have not migrated to the
    context-manager form. Prefer :func:`mock_providers` -- it
    guarantees state restoration even when the test body raises.
    Returns ``(app_state, original)`` so the caller can manually
    restore in a ``finally`` block.
    """
    app_state = test_client.app.state.app_state
    original = app_state._provider_management
    app_state._provider_management = _build_mock_provider_management()
    return app_state, original
