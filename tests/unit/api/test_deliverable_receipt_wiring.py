"""Wiring tests for the deliverable-receipt startup helper.

Regression guard for the red-team signal: ``_wire_deliverable_receipts``
must thread the red-team report store published on
``SecurityStateSlice`` into ``build_deliverable_receipt_service`` so a
completed deliverable's receipt can snapshot the run's red-team findings.
The bug this guards against omitted ``redteam_reports=`` entirely, so
every production receipt carried ``red_team=None`` even with red-team
enabled.
"""

from collections.abc import Callable
from typing import Any

import pytest

from synthorg.api.lifecycle_helpers.deliverable_receipt_wiring import (
    _wire_deliverable_receipts,
)
from synthorg.api.state import AppState
from synthorg.deliverable_receipts.service import DeliverableReceiptService
from synthorg.deliverable_receipts.state_slice import DeliverableReceiptStateSlice
from synthorg.docs_engine.service import DocsService
from synthorg.docs_engine.state import DocsStateSlice
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.security.redteam.report_repo import InMemoryRedTeamReportRepository
from synthorg.security.state import SecurityStateSlice
from tests._shared import FakeClock, make_app_state, mock_of

pytestmark = pytest.mark.unit

_FACTORY_TARGET = (
    "synthorg.deliverable_receipts.factory.build_deliverable_receipt_service"
)


def _capture_factory(captured: dict[str, Any]) -> Callable[..., Any]:
    """Build a stand-in receipt factory that records its kwargs.

    Returns:
        A callable matching ``build_deliverable_receipt_service`` that
        captures the keyword arguments and returns a spec'd service.
    """

    def _factory(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return mock_of[DeliverableReceiptService]()

    return _factory


def _app_state(*, red_team_reports: InMemoryRedTeamReportRepository | None) -> AppState:
    """Build a thin app state with persistence + docs wired and the slice set.

    Returns:
        An ``AppState`` ready for ``_wire_deliverable_receipts``.
    """
    return make_app_state(
        persistence=mock_of[PersistenceBackend](),
        clock=FakeClock(),
        slices={
            DocsStateSlice: {"service": mock_of[DocsService]()},
            SecurityStateSlice: {"red_team_reports": red_team_reports},
        },
    )


async def test_wiring_threads_red_team_repo_into_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A red-team store on the security slice reaches the receipt factory."""
    captured: dict[str, Any] = {}
    monkeypatch.setattr(_FACTORY_TARGET, _capture_factory(captured))
    repo = InMemoryRedTeamReportRepository()
    app_state = _app_state(red_team_reports=repo)

    await _wire_deliverable_receipts(app_state)

    assert captured["redteam_reports"] is repo
    assert app_state.slice(DeliverableReceiptStateSlice).service is not None


async def test_wiring_passes_none_when_no_red_team_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no red-team store wired, the factory receives ``None`` (not omitted)."""
    captured: dict[str, Any] = {}
    monkeypatch.setattr(_FACTORY_TARGET, _capture_factory(captured))
    app_state = _app_state(red_team_reports=None)

    await _wire_deliverable_receipts(app_state)

    assert captured["redteam_reports"] is None
