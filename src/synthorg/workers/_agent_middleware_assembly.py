# module-kind: code
"""Boot assembly for the agent middleware chain.

Registers the agent middleware factories and builds the chain wired into
the boot :class:`AgentEngine`, gated by ``engine.enable_agent_middleware``
(baked in at startup). The chain's ``before_agent`` / ``after_agent``
hooks fire at the engine execution boundary; the headline live effect is
authority-deference defence on the agent path.

Imports of the middleware subsystem are kept lazy (inside the function)
so the boot path does not add an ``engine`` -> ``engine.middleware``
module-level edge, mirroring the coordination chain wiring.
"""

from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.middleware import MIDDLEWARE_CHAIN_BUILT
from synthorg.settings.bootstrap_resolver import resolve_init_value
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import parse_bool

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.budget.coordination_config import ErrorTaxonomyConfig
    from synthorg.engine.middleware.protocol import AgentMiddlewareChain

logger = get_logger(__name__)

_ENABLE_KEY = "enable_agent_middleware"


def build_agent_middleware_chain_or_none(
    app_state: AppState,
    *,
    error_taxonomy_config: ErrorTaxonomyConfig | None,
) -> AgentMiddlewareChain | None:
    """Build the boot agent middleware chain, or ``None`` when disabled.

    Returns:
        The composed :class:`AgentMiddlewareChain` when
        ``engine.enable_agent_middleware`` is set, otherwise ``None``
        (the engine runs with no middleware chain).
    """
    enabled = bool(
        resolve_init_value(
            SettingNamespace.ENGINE,
            _ENABLE_KEY,
            parse=parse_bool,
        ).value
    )
    if not enabled:
        return None

    from synthorg.approval.state import ApprovalStateSlice  # noqa: PLC0415
    from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415
    from synthorg.core.middleware_config import (  # noqa: PLC0415
        AgentMiddlewareConfig,
    )
    from synthorg.engine.middleware._defaults import (  # noqa: PLC0415
        register_agent_defaults,
    )
    from synthorg.engine.middleware.factory import (  # noqa: PLC0415
        build_agent_middleware_chain,
    )

    register_agent_defaults()
    # RootConfig does not surface a middleware field, so the chain uses
    # the default AgentMiddlewareConfig (mirroring the coordination chain
    # wiring); the authority-deference defaults are the live tuning knob.
    config = AgentMiddlewareConfig()
    deps: dict[str, object] = {
        "tracker": app_state.slice(BudgetStateSlice).cost_tracker,
        "approval_gate": app_state.slice(ApprovalStateSlice).gate,
        "error_taxonomy_config": error_taxonomy_config,
        "config": config.authority_deference,
    }
    try:
        return build_agent_middleware_chain(config, deps=deps)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        # ERROR, not WARNING: a failed build silently disables the
        # authority-deference defence for the process lifetime.
        logger.error(
            MIDDLEWARE_CHAIN_BUILT,
            note="agent middleware chain build failed; engine runs without it",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
