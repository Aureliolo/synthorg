"""Correlation ID management for structured logging.

Uses structlog's contextvars integration for async-safe context
propagation across agent actions, tasks, and API requests.

.. note::

    All binding functions are safe to call from both sync and async
    code because Python's :mod:`contextvars` is natively async-aware.
"""

import functools
import inspect
import uuid
from typing import TYPE_CHECKING, ParamSpec, TypeVar

import structlog

from synthorg.observability import get_logger
from synthorg.observability.events.correlation import (
    CORRELATION_ASYNC_DECORATOR_MISUSE,
    CORRELATION_SYNC_DECORATOR_MISUSE,
)

logger = get_logger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

_P = ParamSpec("_P")
_T = TypeVar("_T")


def _build_bindings(
    request_id: str | None,
    task_id: str | None,
    agent_id: str | None,
) -> dict[str, str]:
    """Build a contextvars bindings dict from non-None correlation IDs."""
    bindings: dict[str, str] = {}
    if request_id is not None:
        bindings["request_id"] = request_id
    if task_id is not None:
        bindings["task_id"] = task_id
    if agent_id is not None:
        bindings["agent_id"] = agent_id
    return bindings


def generate_correlation_id() -> str:
    """Generate a new correlation ID.

    Returns:
        A UUID4 string suitable for use as a correlation identifier.
    """
    return str(uuid.uuid4())


def bind_correlation_id(
    *,
    request_id: str | None = None,
    task_id: str | None = None,
    agent_id: str | None = None,
) -> None:
    """Bind correlation IDs to the current context.

    Only non-``None`` values are bound.  Existing bindings for
    unspecified keys are left unchanged.

    Args:
        request_id: Request correlation identifier.
        task_id: Task correlation identifier.
        agent_id: Agent correlation identifier.
    """
    bindings: dict[str, str] = {}
    if request_id is not None:
        bindings["request_id"] = request_id
    if task_id is not None:
        bindings["task_id"] = task_id
    if agent_id is not None:
        bindings["agent_id"] = agent_id
    if bindings:
        structlog.contextvars.bind_contextvars(**bindings)


def unbind_correlation_id(
    *,
    request_id: bool = False,
    task_id: bool = False,
    agent_id: bool = False,
) -> None:
    """Remove specific correlation IDs from the current context.

    Args:
        request_id: Whether to unbind the ``request_id`` key.
        task_id: Whether to unbind the ``task_id`` key.
        agent_id: Whether to unbind the ``agent_id`` key.
    """
    keys: list[str] = []
    if request_id:
        keys.append("request_id")
    if task_id:
        keys.append("task_id")
    if agent_id:
        keys.append("agent_id")
    if keys:
        structlog.contextvars.unbind_contextvars(*keys)


def clear_correlation_ids() -> None:
    """Remove all correlation IDs from the current context.

    Unbinds ``request_id``, ``task_id``, and ``agent_id``.  Other
    context variables are preserved.
    """
    structlog.contextvars.unbind_contextvars(
        "request_id",
        "task_id",
        "agent_id",
    )


def with_correlation(
    *,
    request_id: str | None = None,
    task_id: str | None = None,
    agent_id: str | None = None,
) -> Callable[[Callable[_P, _T]], Callable[_P, _T]]:
    """Decorator that binds correlation IDs for a function's duration.

    Correlation IDs are bound before the function executes and unbound
    after it returns or raises.  Only non-``None`` IDs are managed.

    Note:
        This decorator is for **synchronous** functions only.  Applying
        it to an ``async def`` function raises :exc:`TypeError`.  For
        async functions, use :func:`with_correlation_async` instead.

    Args:
        request_id: Request correlation identifier to bind.
        task_id: Task correlation identifier to bind.
        agent_id: Agent correlation identifier to bind.

    Returns:
        A decorator that manages correlation ID lifecycle.

    Raises:
        TypeError: If the decorated function is a coroutine function.
    """

    def decorator(func: Callable[_P, _T]) -> Callable[_P, _T]:
        if inspect.iscoroutinefunction(func):
            msg = (
                "with_correlation() does not support async functions. "
                "Use with_correlation_async() instead."
            )
            logger.warning(
                CORRELATION_SYNC_DECORATOR_MISUSE,
                function=func.__qualname__,
            )
            raise TypeError(msg)

        @functools.wraps(func)
        def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _T:
            bindings = _build_bindings(request_id, task_id, agent_id)
            with structlog.contextvars.bound_contextvars(**bindings):
                return func(*args, **kwargs)

        return wrapper

    return decorator


def with_correlation_async(
    *,
    request_id: str | None = None,
    task_id: str | None = None,
    agent_id: str | None = None,
) -> Callable[
    [Callable[_P, Coroutine[object, object, _T]]],
    Callable[_P, Coroutine[object, object, _T]],
]:
    """Decorator that binds correlation IDs for an async function's duration.

    Correlation IDs are bound before the coroutine executes and unbound
    after it returns or raises.  Only non-``None`` IDs are managed.

    Note:
        This decorator is for **async** functions only.  Applying it to
        a synchronous function raises :exc:`TypeError`.  For sync
        functions use :func:`with_correlation`.

    Args:
        request_id: Request correlation identifier to bind.
        task_id: Task correlation identifier to bind.
        agent_id: Agent correlation identifier to bind.

    Returns:
        A decorator that manages correlation ID lifecycle for async
        functions.

    Raises:
        TypeError: If the decorated function is not a coroutine function.
    """

    def decorator(
        func: Callable[_P, Coroutine[object, object, _T]],
    ) -> Callable[_P, Coroutine[object, object, _T]]:
        if not inspect.iscoroutinefunction(func):
            msg = (
                "with_correlation_async() requires an async function. "
                "Use with_correlation() for synchronous functions."
            )
            logger.warning(
                CORRELATION_ASYNC_DECORATOR_MISUSE,
                function=func.__qualname__,
            )
            raise TypeError(msg)

        @functools.wraps(func)
        async def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _T:
            bindings = _build_bindings(request_id, task_id, agent_id)
            with structlog.contextvars.bound_contextvars(**bindings):
                return await func(*args, **kwargs)

        return wrapper

    return decorator
