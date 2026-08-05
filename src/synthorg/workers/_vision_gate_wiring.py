# module-kind: code
"""Boot wiring for the vision verifier gate.

The gate is the one runtime collaborator whose construction turns on a
model choice rather than a service reference: the ``heuristic`` / ``noop``
verifiers need only a workspace, while ``llm_vision`` needs a connection
serving a model that can actually see, which only the operator knows. That
resolution and its degrade-to-``None`` path live here so the engine
assembly stays about assembling the engine.
"""

from pathlib import Path

from synthorg.api.state import AppState
from synthorg.budget.state import BudgetStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.providers.state import ProvidersStateSlice
from synthorg.security.visionverify.protocol import VisionVerifierGate
from synthorg.settings.bound_model import resolve_bound_model

logger = get_logger(__name__)


async def build_vision_gate_or_none(
    *,
    app_state: AppState,
    workspace_root: Path,
) -> VisionVerifierGate | None:
    """Construct the vision verifier gate when the subsystem is enabled.

    Pulls :class:`VisionVerifyConfig` from
    ``app_state.config.security.vision_verify``. The ``heuristic`` /
    ``noop`` verifiers need only the workspace; the ``llm_vision``
    verifier additionally needs the operator's
    ``security.vision_verify_model`` pair, because only they know which of
    their registered connections serves a model that can see. An
    ``llm_vision`` selection with no such pair degrades the gate to ``None``
    with a warning rather than crashing boot or guessing a model id.

    Returns:
        The ``VisionVerifierGate`` when the subsystem is enabled and
        buildable, otherwise ``None``.
    """
    from synthorg.security.visionverify.builder import (  # noqa: PLC0415
        build_vision_verifier_gate,
    )

    registry = app_state.slice(ProvidersStateSlice).registry
    model = await resolve_bound_model(
        app_state,
        namespace="security",
        key="vision_verify_model",
        unset_event=API_APP_STARTUP,
    )
    if model is not None and (registry is None or model.provider not in registry):
        logger.warning(
            API_APP_STARTUP,
            service="runtime_services",
            note="configured vision-verify connection is not registered",
            provider_name=model.provider,
        )
        model = None
    try:
        return build_vision_verifier_gate(
            app_state.config.security.vision_verify,
            workspace=workspace_root,
            connections=registry.get if registry is not None else None,
            model=model,
            cost_tracker=app_state.slice(BudgetStateSlice).cost_tracker,
            clock=app_state.clock,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- degrade-to-None wiring
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="runtime_services",
            note="vision verifier gate disabled: build failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None


__all__ = ["build_vision_gate_or_none"]
