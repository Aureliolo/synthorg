# module-kind: code
"""One owner for "is this session out of budget".

Six modules carried a private copy of that rule, each reading accumulated
cost alone. Against a provider that bills by flat subscription cost never
rises, so every one of them was inert in exactly the case a bound matters:
the session's only remaining limit was its turn cap. A rule with several
private copies is also how a fix reaches one path and not the others, which
is how a streamed empty turn came to be relabelled an error while the
buffered path reported it correctly.

So the rule lives here once, in both units. Tokens are counted on every
provider, billed or not, so the token bound is the one that always applies;
the money bound stays because a metered estate wants its own tuned number
per session kind.
"""

from collections.abc import Callable
from typing import Final, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import SETTINGS_FETCH_FAILED
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

_NS: Final[str] = "budget"
_SESSION_TOKEN_CEILING_KEY: Final[str] = "session_token_ceiling"  # noqa: S105 -- setting key, not a secret

#: Mirrors the registered default of ``budget.session_token_ceiling``. Used
#: only where no resolver is wired (test harness, anonymous boot): a session
#: with no bound at all is the state this whole seam exists to remove.
DEFAULT_SESSION_TOKEN_CEILING: Final[int] = 2_000_000


class SessionCeilings(BaseModel):
    """Both bounds on a helper session, travelling together.

    Paired rather than passed as two arguments so a wiring path cannot carry
    one and drop the other: a session bounded only in money is unbounded
    against a provider that bills by flat subscription, which is the state
    this seam exists to remove.

    Attributes:
        cost_ceiling: Money bound in the configured currency; 0 disables it.
        token_ceiling: Token bound; 0 disables it.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    cost_ceiling: float = Field(ge=0.0, description="Money bound; 0 disables")
    token_ceiling: int = Field(ge=0, description="Token bound; 0 disables")


async def resolve_session_token_ceiling(
    resolver: ConfigResolverProtocol | None,
) -> int:
    """Resolve the live per-session token bound.

    One reader for one setting: every bounded helper session asks here, so a
    change to what the bound is cannot reach four sessions and miss the fifth.

    Args:
        resolver: The stage's config resolver, or ``None``.

    Returns:
        The configured ceiling, or :data:`DEFAULT_SESSION_TOKEN_CEILING` when
        no resolver is wired or the read fails.
    """
    if resolver is None:
        return DEFAULT_SESSION_TOKEN_CEILING
    try:
        return await resolver.get_int(_NS, _SESSION_TOKEN_CEILING_KEY)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            SETTINGS_FETCH_FAILED,
            namespace=_NS,
            key=_SESSION_TOKEN_CEILING_KEY,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return DEFAULT_SESSION_TOKEN_CEILING


@runtime_checkable
class _RunningUsage(Protocol):
    """Usage leaf of :class:`_SessionContext`."""

    @property
    def cost(self) -> float:
        """Accumulated cost so far for the session."""
        ...

    @property
    def total_tokens(self) -> int:
        """Accumulated input + output tokens so far for the session."""
        ...


@runtime_checkable
class _SessionContext(Protocol):
    """Structural view of the run context a session checker reads.

    Typed structurally rather than against ``engine.context.AgentContext``
    because ``engine`` imports ``budget``; naming the concrete class here
    would close an ``engine`` -> ``budget`` -> ``engine`` cold cycle.
    """

    @property
    def accumulated_cost(self) -> _RunningUsage:
        """Running token usage and cost totals."""
        ...


def build_session_budget_checker(
    *,
    cost_ceiling: float | None,
    token_ceiling: int | None,
) -> Callable[[_SessionContext], bool] | None:
    """Build the halt predicate for a bounded helper session.

    Args:
        cost_ceiling: Money bound in the configured currency; ``None`` or a
            non-positive value disables it. It measures nothing against a
            provider that bills by flat subscription.
        token_ceiling: Token bound; ``None`` or a non-positive value disables
            it. Counted on every provider, so this is the bound that always
            applies.

    Returns:
        A predicate that is ``True`` once either bound is reached, or
        ``None`` when neither bound is set. ``None`` rather than a
        never-true predicate so a caller can tell "no bound" from "a bound
        not yet reached".
    """
    money = cost_ceiling if cost_ceiling is not None and cost_ceiling > 0 else None
    tokens = token_ceiling if token_ceiling is not None and token_ceiling > 0 else None
    if money is None and tokens is None:
        return None

    def _check(ctx: _SessionContext) -> bool:
        usage = ctx.accumulated_cost
        if tokens is not None and usage.total_tokens >= tokens:
            return True
        return money is not None and usage.cost >= money

    return _check
