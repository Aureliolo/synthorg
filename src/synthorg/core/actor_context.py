"""Actor-identity context seam.

A :mod:`contextvars`-backed seam for *who* is acting (the actor whose
identity an audit row or state-transition log must attribute), bound
once at the boundary where a logical actor context begins and read at
the leaf that records it. Structurally parallel to
:mod:`synthorg.observability.correlation` but independent of structlog:
the bound value is a typed domain object consumed by audit writes, not
a log binding.

The contextvar holds ``None`` outside any bound scope.
:func:`current_actor` never invents a human; a leaf that requires an
actor and finds none must raise, and system-initiated paths bind an
explicit :meth:`ActorIdentity.system` sentinel so a missing binding
fails loudly rather than mis-attributing.

All binding helpers are safe from both sync and async code because
:mod:`contextvars` is natively async-aware.
"""

import functools
import inspect
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, ParamSpec, TypeVar

from pydantic import BaseModel, ConfigDict

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.core.types import NotBlankStr  # noqa: TC001

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Iterator

_P = ParamSpec("_P")
_T = TypeVar("_T")


class ActorKind(StrEnum):
    """What kind of actor is acting.

    Attributes:
        HUMAN: A human operator authenticated at the API boundary.
        SYSTEM: An automated process acting on its own (e.g. an
            approval-timeout scheduler) with no human in the loop.
        AGENT: An autonomous agent acting within a task / workflow.
    """

    HUMAN = "human"
    SYSTEM = "system"
    AGENT = "agent"


class ActorIdentity(BaseModel):
    """The actor whose identity audit writes must attribute.

    Attributes:
        actor_id: Stable identifier (user id, agent id, or a system
            process label).
        kind: Which :class:`ActorKind` is acting.
        label: Optional human-readable descriptor for logs.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor_id: NotBlankStr
    kind: ActorKind
    label: str | None = None

    @classmethod
    def system(cls, label: NotBlankStr) -> ActorIdentity:
        """Build a SYSTEM actor for an automated, human-less path.

        Args:
            label: Names the process (e.g. ``"approval-timeout"``);
                also used as ``actor_id`` so audit rows are traceable
                to the originating subsystem.

        Returns:
            A frozen SYSTEM :class:`ActorIdentity`.
        """
        return cls(actor_id=label, kind=ActorKind.SYSTEM, label=label)


_actor_var: ContextVar[ActorIdentity | None] = ContextVar(
    "synthorg_actor",
    default=None,
)


def bind_actor(actor: ActorIdentity) -> None:
    """Bind *actor* as the current actor for this context.

    Overwrites any prior binding. Use :func:`actor_scope` when the
    prior value must be restored on exit (nested actor contexts).

    Args:
        actor: The identity to bind.
    """
    _actor_var.set(actor)


def current_actor() -> ActorIdentity | None:
    """Return the bound actor, or ``None`` outside any bound scope.

    A ``None`` return is never silently coerced to a human: callers
    that require an actor raise, and system paths bind
    :meth:`ActorIdentity.system` explicitly.
    """
    return _actor_var.get()


class ActorContextMissingError(DomainError):
    """A decision leaf required an actor but none was bound.

    A 500: every decision path is reached either through the
    authenticated HTTP boundary (where ``AuthContextMiddleware`` binds
    the human actor) or through a system path that binds
    :meth:`ActorIdentity.system` explicitly. An unbound read is a
    wiring bug, not a client error -- the seam deliberately refuses to
    invent a human.
    """

    default_message: ClassVar[str] = "Actor context is not bound"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    status_code: ClassVar[int] = 500


def require_actor() -> ActorIdentity:
    """Return the bound actor, raising if none is bound.

    Use at a decision leaf that must attribute an actor. The seam
    never coerces ``None`` to a human; an unbound context is a wiring
    bug surfaced as :class:`ActorContextMissingError`.

    Raises:
        ActorContextMissingError: If no actor is bound.
    """
    actor = _actor_var.get()
    if actor is None:
        raise ActorContextMissingError
    return actor


def resolve_decided_by(explicit: str | None = None) -> str:
    """Resolve the ``decided_by`` attribution string (RFC#3).

    Precedence: an explicit system-override argument wins (used by
    automated paths such as the approval-timeout scheduler);
    otherwise the bound actor's human-readable identity
    (``label`` if set, else ``actor_id``) is used so the value matches
    what callers historically threaded (the username), keeping
    self-review (``decided_by == task.assigned_to``) byte-for-byte
    unchanged.

    Args:
        explicit: Optional caller-supplied override for non-HTTP /
            system decision paths.

    Returns:
        The decider identity string.

    Raises:
        ActorContextMissingError: If no override and no bound actor.
    """
    if explicit is not None:
        return explicit
    actor = require_actor()
    return actor.label or actor.actor_id


def clear_actor() -> None:
    """Remove any bound actor from the current context."""
    _actor_var.set(None)


@contextmanager
def actor_scope(actor: ActorIdentity) -> Iterator[None]:
    """Bind *actor* for the block, restoring the prior value on exit.

    Safe for nested execution contexts (e.g. a system-initiated
    sub-decision inside a human-driven request).

    Args:
        actor: The identity to bind for the duration of the block.
    """
    token = _actor_var.set(actor)
    try:
        yield
    finally:
        _actor_var.reset(token)


def with_actor(
    actor: ActorIdentity,
) -> Callable[[Callable[_P, _T]], Callable[_P, _T]]:
    """Decorator binding *actor* for a synchronous function's duration.

    Note:
        Synchronous functions only. Applying it to an ``async def``
        raises :exc:`TypeError`; use :func:`with_actor_async`.

    Args:
        actor: The identity to bind around each call.

    Returns:
        A decorator managing the actor binding lifecycle.

    Raises:
        TypeError: If the decorated function is a coroutine function.
    """

    def decorator(func: Callable[_P, _T]) -> Callable[_P, _T]:
        if inspect.iscoroutinefunction(func):
            msg = (
                "with_actor() does not support async functions. "
                "Use with_actor_async() instead."
            )
            raise TypeError(msg)

        @functools.wraps(func)
        def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _T:
            with actor_scope(actor):
                return func(*args, **kwargs)

        return wrapper

    return decorator


def with_actor_async(
    actor: ActorIdentity,
) -> Callable[
    [Callable[_P, Coroutine[object, object, _T]]],
    Callable[_P, Coroutine[object, object, _T]],
]:
    """Decorator binding *actor* for an async function's duration.

    Note:
        Async functions only. Applying it to a sync function raises
        :exc:`TypeError`; use :func:`with_actor`.

    Args:
        actor: The identity to bind around each call.

    Returns:
        A decorator managing the actor binding lifecycle for async
        functions.

    Raises:
        TypeError: If the decorated function is not a coroutine
            function.
    """

    def decorator(
        func: Callable[_P, Coroutine[object, object, _T]],
    ) -> Callable[_P, Coroutine[object, object, _T]]:
        if not inspect.iscoroutinefunction(func):
            msg = (
                "with_actor_async() requires an async function. "
                "Use with_actor() for synchronous functions."
            )
            raise TypeError(msg)

        @functools.wraps(func)
        async def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _T:
            with actor_scope(actor):
                return await func(*args, **kwargs)

        return wrapper

    return decorator
