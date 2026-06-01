# module-kind: controller
"""Learning controller -- benchmark learning curve (read-only).

Serves the curve assembled from the per-run scorecard summaries the
golden-company benchmark records into the configured history directory
(``meta.scorecard_history_dir``). The curve model is owned in-package by
:mod:`synthorg.meta.learning_curve`, so this read-only endpoint and the
in-app self-improvement feedback loop share one contract without
depending on the out-of-package ``evals`` layer.
"""

import asyncio
from pathlib import Path

from litestar import Controller, get
from litestar.datastructures import State

from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.meta.learning_curve import LearningCurve, read_learning_curve
from synthorg.observability import get_logger
from synthorg.observability.events.meta import META_LEARNING_CURVE_QUERIED
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)


class LearningController(Controller):
    """Read-only benchmark learning curve."""

    path = "/learning"
    tags = ("learning",)

    @get(
        "/curve",
        guards=[
            require_read_access,
            per_op_rate_limit_from_policy("learning.curve"),
        ],
    )
    async def get_curve(self, state: State) -> ApiResponse[LearningCurve]:
        """Return the benchmark learning curve across recorded runs.

        Reads the per-run scorecard summaries from the configured history
        directory (``meta.scorecard_history_dir``) and assembles the
        chronological curve with per-run deltas and regression flags. An
        unset directory, or one with no recorded runs, yields an empty
        curve -- a legitimate "no benchmark history yet" state, not a
        service failure.

        Args:
            state: Application state.

        Returns:
            The learning-curve envelope.
        """
        app_state: AppState = state.app_state
        history_dir = await config_resolver_of(app_state).get_str(
            SettingNamespace.META, "scorecard_history_dir"
        )
        curve = (
            await asyncio.to_thread(read_learning_curve, Path(history_dir))
            if history_dir
            else LearningCurve()
        )
        logger.debug(
            META_LEARNING_CURVE_QUERIED,
            point_count=len(curve.points),
            has_regression=curve.has_regression,
        )
        return ApiResponse(data=curve)
