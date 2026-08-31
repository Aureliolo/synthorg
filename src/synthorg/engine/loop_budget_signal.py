# module-kind: code
"""Turn-boundary signal reporting a run's live spend against its ceiling.

The system prompt declares the ceiling once, at zero spend, because that is
the one fact true for the life of the session; only the turn boundary can
report what has actually been spent, and only there does the number change.
Reported at declared steps rather than every turn: the recording that
motivated this issue ran with no prompt caching on the connection and burn
quadratic in turn count, so an injected line on all 130 turns of one merge
would be real, avoidable spend, and a line repeating every turn reads as flat
telemetry rather than as a constraint that changes behaviour.

Mirrors ``background_job_watch.py``'s shape: a pure function consulted at the
existing turn-boundary slot in ``react_loop.execute``, returning an updated
context or ``None``.
"""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.engine.context import AgentContext
from synthorg.engine.context_budget import make_context_indicator
from synthorg.engine.loop_budget_defaults import (
    DEFAULT_BUDGET_SIGNAL_STEP_PERCENT,
    DEFAULT_BUDGET_SIGNAL_TERMINAL_PERCENT,
)
from synthorg.observability import get_logger
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage
from synthorg.settings.kill_switch import resolve_int_with_fallback
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

_ENGINE_NAMESPACE = "engine"
_STEP_PERCENT_KEY = "budget_signal_step_percent"
_TERMINAL_PERCENT_KEY = "budget_signal_terminal_percent"


class BudgetSignalConfig(BaseModel):
    """Both knobs governing the turn-boundary budget signal.

    Attributes:
        step_percent: Interval, in percent of the ceiling, at which the
            signal fires once; 0 disables the periodic signal (the terminal
            warning below still fires).
        terminal_percent: Share at and past which the signal fires every
            turn with a terminal warning.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    step_percent: int = Field(
        ge=0, le=100, description="Percent interval between reports"
    )
    terminal_percent: int = Field(
        gt=0, le=100, description="Percent past which every turn warns"
    )


async def resolve_budget_signal_config(
    config_resolver: ConfigResolverProtocol | None,
) -> BudgetSignalConfig:
    """Resolve the live budget-signal thresholds.

    Read per run, beside ``resolve_turn_extensions``: an operator's change
    takes effect on the next dispatch rather than the next restart. Each key
    resolves through its own :func:`resolve_int_with_fallback` call, on the
    same convention every other live-resolved setting in the tree uses, so a
    resolver outage on one key is attributable (``SETTINGS_FETCH_FAILED``
    names the failing ``namespace``/``key``) rather than leaving both
    thresholds unexplained.

    Args:
        config_resolver: Settings resolver, or ``None`` when unwired.

    Returns:
        The operator-configured thresholds, or the declared defaults when no
        resolver is wired or a read fails.
    """
    step_percent = await resolve_int_with_fallback(
        resolver=config_resolver,
        namespace=_ENGINE_NAMESPACE,
        key=_STEP_PERCENT_KEY,
        fallback=DEFAULT_BUDGET_SIGNAL_STEP_PERCENT,
    )
    terminal_percent = await resolve_int_with_fallback(
        resolver=config_resolver,
        namespace=_ENGINE_NAMESPACE,
        key=_TERMINAL_PERCENT_KEY,
        fallback=DEFAULT_BUDGET_SIGNAL_TERMINAL_PERCENT,
    )
    return BudgetSignalConfig(
        step_percent=step_percent, terminal_percent=terminal_percent
    )


def _step_message(ctx: AgentContext, boundary: int) -> str:
    """Word the periodic step report.

    Returns:
        The USER-role message content.
    """
    indicator = make_context_indicator(ctx, source="turn_signal").format()
    return (
        f"{indicator} You have used {boundary}% of your token budget for this "
        "run. If you have not started producing your deliverable, start now; "
        "if you have, keep going."
    )


def _terminal_message(ctx: AgentContext, terminal_percent: int) -> str:
    """Word the terminal warning, repeated every turn past the threshold.

    Returns:
        The USER-role message content.
    """
    indicator = make_context_indicator(ctx, source="turn_signal").format()
    return (
        f"{indicator} You are at or past {terminal_percent}% of your token "
        "budget, close to this run's ceiling. Finish or record what you have "
        "now: the run ends when the ceiling is reached, and further budget is "
        "not automatic."
    )


def check_budget_signal(
    ctx: AgentContext,
    config: BudgetSignalConfig,
) -> AgentContext | None:
    """Report live token spend against ``ctx.token_ceiling``, at declared steps.

    Args:
        ctx: Current agent context.
        config: The resolved step and terminal thresholds.

    Returns:
        The context with the injected message (and, for a step report, the
        crossed boundary recorded), or ``None`` when nothing should be
        reported this turn -- a run with no token ceiling, the periodic
        signal disabled (``step_percent <= 0``) with spend still below the
        terminal share, a spend still below the next undeclared step, or a
        step already announced.
    """
    if ctx.token_ceiling is None:
        return None
    spend_percent = (ctx.accumulated_cost.total_tokens / ctx.token_ceiling) * 100.0
    if spend_percent >= config.terminal_percent:
        message = _terminal_message(ctx, config.terminal_percent)
        return ctx.with_message(ChatMessage(role=MessageRole.USER, content=message))
    if config.step_percent <= 0:
        return None
    boundary = (int(spend_percent) // config.step_percent) * config.step_percent
    if boundary <= 0 or boundary <= ctx.budget_signal_last_step_percent:
        return None
    message = _step_message(ctx, boundary)
    updated = ctx.with_message(ChatMessage(role=MessageRole.USER, content=message))
    return updated.model_copy(update={"budget_signal_last_step_percent": boundary})


__all__ = [
    "BudgetSignalConfig",
    "check_budget_signal",
    "resolve_budget_signal_config",
]
