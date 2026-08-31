# module-kind: code
"""One owner for "is this session out of budget".

The rule lives here once so every bounded session asks the same question and
a change to it reaches all of them, rather than each carrying a private copy
a fix can miss.

It answers in both units. Cost never rises against a provider that bills by
flat subscription, so a cost-only bound is inert in exactly the case a bound
matters and the session's only remaining limit is its turn cap. Tokens are
counted on every provider, billed or not, so the token bound is the one that
always applies; the money bound stays because a metered estate wants its own
tuned number per session kind.
"""

from collections.abc import Callable
from dataclasses import dataclass
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
    this seam exists to remove. Every seam that carries a session's bounds
    carries this: the resolvers that produce them, the configs that hold
    them, and :func:`build_session_budget_checker`, which consumes them.

    Attributes:
        cost_ceiling: Money bound in the configured currency; 0 disables it.
        token_ceiling: Token bound; 0 disables it.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    cost_ceiling: float = Field(ge=0.0, description="Money bound; 0 disables")
    token_ceiling: int = Field(ge=0, description="Token bound; 0 disables")

    @classmethod
    def of(
        cls,
        *,
        cost_ceiling: float | None,
        token_ceiling: int | None,
    ) -> SessionCeilings:
        """Build a pair from two optionals, normalising "unset" to disabled.

        The two bounds arrive as optionals from several places (an
        unconfigured session config, a context field that may be unset, a
        resolver with nothing to read). ``None`` and a non-positive value
        both mean the same thing here, so they collapse to ``0``, and the
        pair itself is what travels onward.

        Args:
            cost_ceiling: Money bound, or ``None`` / non-positive for none.
            token_ceiling: Token bound, or ``None`` / non-positive for none.

        Returns:
            The normalised pair.
        """
        return cls(
            cost_ceiling=cost_ceiling if cost_ceiling and cost_ceiling > 0 else 0.0,
            token_ceiling=token_ceiling if token_ceiling and token_ceiling > 0 else 0,
        )

    @property
    def bounded(self) -> bool:
        """Whether either bound is set."""
        return self.cost_ceiling > 0 or self.token_ceiling > 0


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


@dataclass(frozen=True, slots=True)
class SessionBudgetChecker:
    """A budget predicate that publishes the ceilings it enforces.

    A loop consumes this as a plain ``Callable[[ctx], bool]`` every turn; a
    prompt or a turn-boundary signal consumes :attr:`ceilings` once, before
    the loop starts, to render what the session's bound actually IS. Two
    return values from two different builders is how a published ceiling and
    an enforced one come apart; carrying both here means whoever renders the
    gauge reads the exact pair the predicate below is closed over, not a
    second resolution of it.

    Attributes:
        ceilings: The bound this checker enforces.
    """

    ceilings: SessionCeilings
    _predicate: Callable[[_SessionContext], bool]

    def __call__(self, ctx: _SessionContext) -> bool:
        """Whether *ctx* has exhausted :attr:`ceilings`.

        Returns:
            ``True`` once either bound in :attr:`ceilings` is reached.
        """
        return self._predicate(ctx)


def build_session_budget_checker(
    ceilings: SessionCeilings,
) -> SessionBudgetChecker | None:
    """Build the halt predicate for a bounded helper session.

    Takes the pair rather than two scalars: a caller that has one bound in
    hand and not the other has to say so by constructing the pair, which is
    the omission :class:`SessionCeilings` exists to make visible. Use
    :meth:`SessionCeilings.of` where the bounds arrive as optionals.

    Args:
        ceilings: Both bounds on the session. The money bound measures
            nothing against a provider that bills by flat subscription;
            tokens are counted on every provider, so the token bound is the
            one that always applies.

    Returns:
        A :class:`SessionBudgetChecker` that is ``True`` once either bound is
        reached, or ``None`` when neither bound is set. ``None`` rather than
        a never-true predicate so a caller can tell "no bound" from "a bound
        not yet reached".
    """
    if not ceilings.bounded:
        return None
    money = ceilings.cost_ceiling
    tokens = ceilings.token_ceiling

    def _check(ctx: _SessionContext) -> bool:
        usage = ctx.accumulated_cost
        if tokens > 0 and usage.total_tokens >= tokens:
            return True
        return money > 0 and usage.cost >= money

    return SessionBudgetChecker(ceilings=ceilings, _predicate=_check)
