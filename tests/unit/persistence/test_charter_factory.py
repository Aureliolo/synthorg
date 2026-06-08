"""Tests for the backend-aware charter repository factory.

Focused on the handle-acquisition guard, which is narrowed to
``PersistenceConnectionError`` (the only exception ``get_db`` raises per
the ``PersistenceBackend`` contract): an unavailable handle degrades to
``None`` rather than raising during boot.
"""

from unittest.mock import Mock

import pytest
import structlog.testing

from synthorg.core.persistence_errors import PersistenceConnectionError
from synthorg.persistence.charter_factory import build_charter_repository
from synthorg.persistence.protocol import PersistenceBackend
from tests._shared import mock_of


@pytest.mark.unit
class TestBuildCharterRepository:
    def test_returns_none_when_get_db_not_connected(self) -> None:
        """A connected-looking backend whose ``get_db`` raises degrades to None.

        Pins the narrowed guard: ``PersistenceConnectionError`` is caught,
        logged, and swallowed so the opt-in charter store wiring returns
        ``None`` (caller 503s) instead of raising during boot.
        """
        backend = mock_of[PersistenceBackend](
            backend_name="sqlite",
            is_connected=True,
            get_db=Mock(side_effect=PersistenceConnectionError("not connected")),
        )
        with structlog.testing.capture_logs() as events:
            result = build_charter_repository(backend)
        assert result is None
        backend.get_db.assert_called_once()
        assert any(e.get("error_type") == "PersistenceConnectionError" for e in events)

    def test_returns_none_when_backend_absent(self) -> None:
        """A ``None`` backend short-circuits to ``None`` without touching it."""
        assert build_charter_repository(None) is None
