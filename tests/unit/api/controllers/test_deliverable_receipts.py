"""Controller tests for the deliverable-receipt REST surface.

Exercises the read + validate endpoints over the portal-free async
client: 503 when the service is unwired, 404 when no receipt exists for
the deliverable, and 200 with the receipt / validation payload once a
service backed by in-memory fakes is wired onto the feature slice.
"""

from datetime import UTC, datetime

import pytest

from synthorg.deliverable_receipts.builder import ReceiptBuilder
from synthorg.deliverable_receipts.models import DeliverableReceipt
from synthorg.deliverable_receipts.renderer import ReceiptRenderer
from synthorg.deliverable_receipts.service import DeliverableReceiptService
from synthorg.deliverable_receipts.state_slice import DeliverableReceiptStateSlice
from synthorg.deliverable_receipts.validator import ReceiptValidator
from synthorg.docs_engine.service import DocsService
from synthorg.persistence.docs_protocol import DocsRepository
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrameRepository,
)
from synthorg.persistence.knowledge_protocol import KnowledgeSourceRepository
from tests._shared import LoopAsyncClient, mock_of
from tests.unit.deliverable_receipts._fakes import (
    InMemoryCodeExecutionRecordRepository,
    InMemoryDeliverableReceiptRepository,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)
_PROJECT = "proj-1"
_SLUG = "quarterly-report"


def _receipt(*, slug: str = _SLUG, project_id: str = _PROJECT) -> DeliverableReceipt:
    """Build a minimal, signal-free receipt (validates as trivially valid)."""
    return DeliverableReceipt.model_validate(
        {
            "receipt_id": "r-1",
            "task_id": "t-1",
            "project_id": project_id,
            "execution_id": "exec-1",
            "deliverable_doc_slug": slug,
            "issued_at": _NOW,
            "total_cost": 0.0,
            "currency": "USD",
        },
    )


def _wire_service(
    client: LoopAsyncClient,
    receipts: InMemoryDeliverableReceiptRepository,
) -> None:
    """Wire a receipt service backed by ``receipts`` onto the feature slice.

    The builder, renderer, docs, docs-service, and flight-recorder
    collaborators are unused by the read/validate endpoints, so they are
    typed-boundary mocks; the validator is real (over in-memory fakes) so
    the validate endpoint exercises the genuine consistency checks.
    """
    validator = ReceiptValidator(
        knowledge_sources=mock_of[KnowledgeSourceRepository](),
        code_execution_records=InMemoryCodeExecutionRecordRepository(),
    )
    service = DeliverableReceiptService(
        receipts=receipts,
        builder=mock_of[ReceiptBuilder](),
        validator=validator,
        renderer=mock_of[ReceiptRenderer](),
        docs=mock_of[DocsRepository](),
        docs_service=mock_of[DocsService](),
        flight_recorder=mock_of[FlightRecorderFrameRepository](),
    )
    app_state = client.app.state.app_state
    app_state.wire(DeliverableReceiptStateSlice, service=service)


class TestGetReceipt:
    async def test_unwired_service_returns_503(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """With no receipt service wired, the endpoint reports 503."""
        app_state = async_test_client.app.state.app_state
        app_state.wire(DeliverableReceiptStateSlice, service=None)
        resp = await async_test_client.get(
            f"/api/v1/projects/{_PROJECT}/docs/{_SLUG}/receipt",
        )
        assert resp.status_code == 503

    async def test_missing_receipt_returns_404(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """A wired service with no matching receipt returns 404."""
        _wire_service(async_test_client, InMemoryDeliverableReceiptRepository())
        resp = await async_test_client.get(
            f"/api/v1/projects/{_PROJECT}/docs/{_SLUG}/receipt",
        )
        assert resp.status_code == 404

    async def test_existing_receipt_returns_200(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """A persisted receipt is returned wrapped in the API envelope."""
        receipts = InMemoryDeliverableReceiptRepository()
        await receipts.save(_receipt())
        _wire_service(async_test_client, receipts)

        resp = await async_test_client.get(
            f"/api/v1/projects/{_PROJECT}/docs/{_SLUG}/receipt",
        )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["receipt_id"] == "r-1"
        assert data["deliverable_doc_slug"] == _SLUG
        assert data["project_id"] == _PROJECT

    async def test_receipt_scoped_to_project(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """A receipt in another project is not returned for this project."""
        receipts = InMemoryDeliverableReceiptRepository()
        await receipts.save(_receipt(project_id="other-project"))
        _wire_service(async_test_client, receipts)

        resp = await async_test_client.get(
            f"/api/v1/projects/{_PROJECT}/docs/{_SLUG}/receipt",
        )
        assert resp.status_code == 404


class TestValidateReceipt:
    async def test_validate_missing_receipt_returns_404(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """Validating an absent receipt surfaces the not-found 404."""
        _wire_service(async_test_client, InMemoryDeliverableReceiptRepository())
        resp = await async_test_client.get(
            f"/api/v1/projects/{_PROJECT}/docs/{_SLUG}/receipt/validate",
        )
        assert resp.status_code == 404

    async def test_validate_signal_free_receipt_is_valid(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """A receipt with no signals validates as trivially consistent."""
        receipts = InMemoryDeliverableReceiptRepository()
        await receipts.save(_receipt())
        _wire_service(async_test_client, receipts)

        resp = await async_test_client.get(
            f"/api/v1/projects/{_PROJECT}/docs/{_SLUG}/receipt/validate",
        )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["valid"] is True
        assert data["errors"] == []
