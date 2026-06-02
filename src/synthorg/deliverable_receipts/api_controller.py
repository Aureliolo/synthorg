# module-kind: controller
"""Deliverable-receipt REST controller.

Read-only HTTP surface hung off the living-doc slug: fetch the
provenance receipt for a deliverable and validate its consistency. The
receipt itself is built in-process on deliverable completion; this
controller only reads and validates.
"""

from typing import TYPE_CHECKING

from litestar import Controller, Response, get
from litestar.datastructures import State

from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access
from synthorg.api.path_params import PathId
from synthorg.core.types import NotBlankStr
from synthorg.deliverable_receipts.errors import DeliverableReceiptNotFoundError
from synthorg.deliverable_receipts.models import (
    DeliverableReceipt,
    ReceiptValidationResult,
)
from synthorg.deliverable_receipts.state_slice import deliverable_receipt_service_of
from synthorg.observability import get_logger

if TYPE_CHECKING:
    from synthorg.deliverable_receipts.service import DeliverableReceiptService

logger = get_logger(__name__)


class DeliverableReceiptController(Controller):
    """Read + validate endpoints for deliverable provenance receipts."""

    path = "/projects/{project_id:str}/docs/{slug:str}/receipt"
    tags = ("deliverable_receipts",)

    @get(guards=[require_read_access])
    async def get_receipt(
        self,
        state: State,
        project_id: PathId,
        slug: PathId,
    ) -> Response[ApiResponse[DeliverableReceipt]]:
        """Fetch the provenance receipt for a deliverable.

        Returns:
            ``Response[ApiResponse[DeliverableReceipt]]`` instance.

        Raises:
            DeliverableReceiptNotFoundError: When no receipt exists for
                the deliverable (mapped to 404).
        """
        service = self._service(state)
        receipt = await service.get(
            project_id=NotBlankStr(project_id),
            slug=NotBlankStr(slug),
        )
        if receipt is None:
            msg = f"no receipt for deliverable {slug!r}"
            raise DeliverableReceiptNotFoundError(msg)
        return Response(
            content=ApiResponse[DeliverableReceipt](data=receipt),
            status_code=200,
        )

    @get("/validate", guards=[require_read_access])
    async def validate_receipt(
        self,
        state: State,
        project_id: PathId,
        slug: PathId,
    ) -> Response[ApiResponse[ReceiptValidationResult]]:
        """Validate the deliverable's receipt for consistency.

        Returns:
            ``Response[ApiResponse[ReceiptValidationResult]]`` instance.

        Raises:
            DeliverableReceiptNotFoundError: When no receipt exists for
                the deliverable (mapped to 404).
        """
        service = self._service(state)
        result = await service.validate(
            project_id=NotBlankStr(project_id),
            slug=NotBlankStr(slug),
        )
        return Response(
            content=ApiResponse[ReceiptValidationResult](data=result),
            status_code=200,
        )

    @staticmethod
    def _service(state: State) -> DeliverableReceiptService:
        """Resolve the receipt service from app state (503 when absent).

        Returns:
            The wired ``DeliverableReceiptService``.
        """
        return deliverable_receipt_service_of(state.app_state)
