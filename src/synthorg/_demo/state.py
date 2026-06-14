"""The demo feature's state slice + service accessor."""

from typing import TYPE_CHECKING

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg._demo.service import DemoService

if TYPE_CHECKING:
    from synthorg.api.state_slices import AppStateSliceMixin


class DemoStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the demo feature.

    ``service`` is ``None`` until the construction wirer populates it; the
    demo controller and MCP handler degrade (503 / capability gap) on ``None``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    service: DemoService | None = None


def demo_service_of(app_state: AppStateSliceMixin) -> DemoService:
    """Resolve the demo service from its slice, or raise 503.

    Returns:
        The wired demo service.
    """
    return require_service(
        app_state.slice(DemoStateSlice).service,
        "Demo Service",
    )
