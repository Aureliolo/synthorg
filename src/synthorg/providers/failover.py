# module-kind: code
"""The operator's declared alternate for a system feature's bound pair.

A system feature binds one ``(provider, model)`` pair and has no employee to
mark out when that pair stops serving, which is the one place a declared
fallback earns its keep. An agent has one: work goes to a different agent
(see :mod:`synthorg.engine.roster`), and the gateway has one too, since its
token is minted per agent run.

Two rules keep this from becoming the auto-pick this codebase spent a lot of
effort removing:

- **The operator writes both halves.** Resolution is an exact-key lookup in a
  map an operator authored. Nothing is sorted, indexed, ranked or scanned, so
  there is no arrangement of the provider registry that produces a fallback
  nobody chose.
- **It is off until switched on**, and switching it on is a governed write,
  because it widens what may serve a bound request.

Failing over is never silent: every engagement is logged AND persisted, so
the question "which connection actually answered this" survives the restart
the log does not.
"""

import json
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from synthorg.observability import get_logger
from synthorg.observability.events.provider import (
    PROVIDER_FAILOVER_ROUTES_UNREADABLE,
)
from synthorg.providers.health import ProviderOutcomeClass
from synthorg.settings.model_ref import ModelRef, parse_model_ref

logger = get_logger(__name__)

#: Failures the alternate has a real chance of surviving: the declared pair
#: is unwell (5xx, queueing, throttled, timed out, unreachable) or its
#: balance is empty, and none of those say anything about the request.
#:
#: Everything else is deliberately absent. An invalid request, a bad key, a
#: content filter or an unknown model would fail identically on the
#: alternate, so retrying there buys nothing and costs the caller a second
#: round-trip on top of the first.
RETRYABLE_ON_ALTERNATE: Final[frozenset[ProviderOutcomeClass]] = frozenset(
    {
        ProviderOutcomeClass.INTERNAL,
        ProviderOutcomeClass.OVERLOADED,
        ProviderOutcomeClass.RATE_LIMIT,
        ProviderOutcomeClass.PAYMENT_REQUIRED,
        ProviderOutcomeClass.TIMEOUT,
        ProviderOutcomeClass.CONNECTION,
    }
)


def route_key(ref: ModelRef) -> str:
    """Return the canonical map key for a declared pair.

    Returns:
        ``"provider/model_id"``, the one spelling both the writer and the
        reader use, so a route can only be found by naming the exact pair it
        was declared for.
    """
    return f"{ref.provider.strip()}/{ref.model_id.strip()}"


def parse_route_key(key: str) -> ModelRef:
    """Split a canonical map key back into the pair it names.

    Partitions on the FIRST separator, because a model id routinely carries
    one of its own (``vendor/model-y`` is one model), while a connection
    name never does: it is a registry key.

    Returns:
        The declared pair, or an unbound ref when the key names no pair.
    """
    provider, separator, model_id = key.strip().partition("/")
    if not separator:
        return ModelRef()
    return ModelRef(provider=provider.strip(), model_id=model_id.strip())


class FailoverRoutes:
    """An operator's declared alternates, keyed by the declared pair.

    Args:
        routes: Parsed ``declared -> alternate`` pairs.
    """

    __slots__ = ("_routes",)

    def __init__(self, routes: Mapping[str, ModelRef]) -> None:
        self._routes = MappingProxyType(dict(routes))

    def __len__(self) -> int:
        """Return how many routes the operator declared."""
        return len(self._routes)

    def alternate_for(self, declared: ModelRef) -> ModelRef | None:
        """Return the alternate the operator declared for *declared*.

        An exact-key lookup and nothing else. A pair with no entry has no
        fallback, which is the same answer as the feature being off: there is
        no nearest match, no provider scan, no default.

        Returns:
            The alternate pair, or ``None`` when the operator declared none.
        """
        if not declared.is_bound:
            return None
        alternate = self._routes.get(route_key(declared))
        if alternate is None or not alternate.is_bound:
            return None
        if route_key(alternate) == route_key(declared):
            # A route to itself is not a fallback; serving it would report a
            # failover that changed nothing.
            return None
        return alternate

    def declared_pairs(self) -> tuple[tuple[ModelRef, ModelRef], ...]:
        """Return every ``(declared, alternate)`` route, in key order.

        The declared half is parsed back out of its key here rather than by
        the surface that displays it, so the format has exactly one reader
        and a rendered route cannot drift from the one resolution matches.

        Returns:
            The routes whose declared key names a full pair. A key that does
            not is dropped: it names no dispatch target, and
            :meth:`alternate_for` would never match it either.
        """
        pairs: list[tuple[ModelRef, ModelRef]] = []
        for key in sorted(self._routes):
            declared = parse_route_key(key)
            alternate = self.alternate_for(declared)
            if alternate is not None:
                pairs.append((declared, alternate))
        return tuple(pairs)


EMPTY_ROUTES: Final[FailoverRoutes] = FailoverRoutes({})


def parse_failover_routes(raw: str | None) -> FailoverRoutes:
    """Parse the stored ``providers.failover_routes`` value.

    The stored shape is ``{"provider/model": {"provider": ..., "model_id":
    ...}}``. A malformed blob yields no routes rather than a partial map: a
    half-read fallback table would fail over some pairs and not others with
    nothing saying which, and no failover at all is the safe reading of "we
    could not tell what you declared".

    Returns:
        The declared routes, empty when the value is unset or unreadable.
    """
    if raw is None or not raw.strip():
        return EMPTY_ROUTES
    try:
        parsed = json.loads(raw)
    except ValueError:
        logger.warning(
            PROVIDER_FAILOVER_ROUTES_UNREADABLE,
            reason="not_json",
        )
        return EMPTY_ROUTES
    if not isinstance(parsed, dict):
        logger.warning(
            PROVIDER_FAILOVER_ROUTES_UNREADABLE,
            reason="not_an_object",
        )
        return EMPTY_ROUTES
    routes: dict[str, ModelRef] = {}
    for declared, alternate in parsed.items():
        ref = _as_ref(alternate)
        if ref is None or not ref.is_bound:
            continue
        # Store under the same canonical form the read path builds. The
        # writer's spelling is not the reader's: ``route_key`` trims each
        # half, so a declared key carrying inner whitespace would be stored
        # verbatim, never matched, and never displayed either.
        declared_ref = parse_route_key(str(declared))
        if not declared_ref.is_bound:
            continue
        routes[route_key(declared_ref)] = ref
    return FailoverRoutes(routes)


def _as_ref(value: object) -> ModelRef | None:
    """Coerce one stored alternate into a :class:`ModelRef`.

    Returns:
        The parsed ref, or ``None`` when the value names no pair. A
        provider-less alternate is refused here rather than resolved later:
        the same model id through two connections is two different calls, so
        an alternate with no connection names no dispatch target.
    """
    if isinstance(value, str):
        return parse_model_ref(value)
    if isinstance(value, dict):
        provider = value.get("provider")
        model_id = value.get("model_id")
        if isinstance(provider, str) and isinstance(model_id, str):
            return ModelRef(provider=provider, model_id=model_id)
    return None


__all__ = [
    "EMPTY_ROUTES",
    "RETRYABLE_ON_ALTERNATE",
    "FailoverRoutes",
    "parse_failover_routes",
    "parse_route_key",
    "route_key",
]
