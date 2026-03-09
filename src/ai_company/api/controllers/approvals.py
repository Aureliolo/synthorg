"""Approvals controller (stub — implemented in M7)."""

from litestar import Controller, Response, get
from litestar.datastructures import State  # noqa: TC002

from ai_company.api.dto import ApiResponse


class ApprovalsController(Controller):
    """Stub controller for human approval queue.

    Full implementation is planned for M7 (Security & HR).
    """

    path = "/approvals"
    tags = ("approvals",)

    @get()
    async def list_approvals(
        self,
        state: State,  # noqa: ARG002
    ) -> Response[ApiResponse[None]]:
        """List pending approvals (stub → 501).

        Args:
            state: Application state.

        Returns:
            Not implemented response.
        """
        return Response(
            content=ApiResponse[None](
                success=False,
                error="Approval queue not implemented yet (M7)",
            ),
            status_code=501,
        )
