# module-kind: controller
"""The demo feature's REST controller: one read-only endpoint."""

from litestar import Controller, get
from litestar.datastructures import State

from synthorg._demo.service import DemoGreeting
from synthorg._demo.state import demo_service_of
from synthorg.api.dto import ApiResponse
from synthorg.api.state import AppState


class DemoController(Controller):
    """A single read-only endpoint proving feature-owned route discovery."""

    path = "/demo"
    tags = ("demo",)

    @get()
    async def get_greeting(self, state: State) -> ApiResponse[DemoGreeting]:
        """Return the demo greeting.

        Args:
            state: Application state.

        Returns:
            The greeting wrapped in the standard API envelope.
        """
        app_state: AppState = state.app_state
        return ApiResponse(data=demo_service_of(app_state).greet())
