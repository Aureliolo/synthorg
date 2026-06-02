"""Policy-honouring typeguard checker for TYPE_CHECKING-only signature types.

typeguard 4.5.2's structural checkers (``check_protocol``, ``check_callable``,
``check_tuple``, ``check_typed_dict``) eagerly evaluate member / signature
annotations via ``inspect.signature`` / ``typing.get_type_hints`` /
``__annotations__``. Under PEP 649 (Python 3.14) deferred annotations, a
NamedTuple field, TypedDict member, Protocol method signature, or passed
callable whose type is only importable under ``if TYPE_CHECKING:`` raises a raw
``NameError`` at check time. ``typeguard._checkers.check_type_internal`` calls
the resolved checker without guarding that call, so the ``NameError`` propagates
out as a test error, and ``--typeguard-forward-ref-policy`` never sees it (that
policy governs only ``ForwardRef`` resolution inside ``check_type_internal``,
not raw ``NameError``\\s from the structural checkers).

``register_policy_honoring_checker`` inserts a lookup at the front of
``typeguard.checker_lookup_functions`` that delegates to the builtin lookup and
wraps whatever checker it returns so a ``NameError`` raised during the check is
routed through ``memo.config.forward_ref_policy``:

- ``WARN``: emit a ``TypeHintWarning`` and skip the check (treat as pass).
- ``IGNORE``: skip silently.
- ``ERROR``: re-raise (the ERROR-hardening posture; not used at WARN).

This makes the eager-eval ``NameError`` class consistent with the forward-ref
policy without migrating every ``TYPE_CHECKING``-guarded signature to a runtime
import. The module imports only ``typeguard`` (no ``synthorg``) so the conftest
can register the checker before installing the import hook, without pulling any
``synthorg`` module into the interpreter ahead of instrumentation.
"""

import warnings
from typing import Any
from unittest.mock import Mock

import typeguard
from typeguard import (
    ForwardRefPolicy,
    TypeCheckerCallable,
    TypeCheckError,
    TypeCheckMemo,
    TypeHintWarning,
)
from typeguard._checkers import builtin_checker_lookup


def _wrap(inner: TypeCheckerCallable) -> TypeCheckerCallable:
    """Return ``inner`` guarded so a check-time ``NameError`` honours the policy.

    A ``TypeCheckError`` (a genuine resolved-type mismatch) still propagates;
    only ``NameError`` (an unresolved ``TYPE_CHECKING``-only signature member) is
    routed through ``memo.config.forward_ref_policy``.
    """

    def _checked(
        value: Any,
        origin_type: Any,
        args: tuple[Any, ...],
        memo: TypeCheckMemo,
    ) -> Any:
        try:
            return inner(value, origin_type, args, memo)
        except NameError as exc:
            policy = memo.config.forward_ref_policy
            if policy is ForwardRefPolicy.ERROR:
                raise
            if policy is ForwardRefPolicy.WARN:
                unresolved = getattr(exc, "name", None)
                warnings.warn(
                    "Skipping type check: a signature or member references the "
                    f"unresolved name {unresolved!r} (TYPE_CHECKING-only); "
                    "resolve it at runtime to enforce this boundary.",
                    TypeHintWarning,
                    stacklevel=2,
                )
            return None

    return _checked


def _policy_honoring_lookup(
    origin_type: Any,
    args: tuple[Any, ...],
    extras: tuple[Any, ...],
) -> TypeCheckerCallable | None:
    """Wrap the builtin checker (if any) so check-time ``NameError`` is policed.

    Returns ``None`` for origins the builtin does not handle, so typeguard's
    own fallback ``isinstance`` logic runs unchanged.
    """
    inner = builtin_checker_lookup(origin_type, args, extras)
    if inner is None:
        return None
    return _wrap(inner)


def _pydantic_generic_lookup(
    origin_type: Any,
    args: tuple[Any, ...],
    extras: tuple[Any, ...],
) -> TypeCheckerCallable | None:
    """Relax a pydantic generic alias (``Model[X]``) to its origin base class.

    pydantic v2 builds a DISTINCT subclass for ``Model[X]`` (both ``Model[T]``
    with a free TypeVar and ``Model[Concrete]``). This codebase constructs these
    snapshots BARE (``VersionSnapshot(...)``, generically, without binding the
    parameter), so the runtime value is a plain ``Model`` instance that is NOT an
    instance of the ``Model[X]`` subclass, and typeguard's fallback ``isinstance``
    check raises. The type argument is erased on bare construction, so the honest
    runtime check is against the origin base class; pydantic has already validated
    the inner fields on construction, and the parameter distinction is a
    static-typing concern (mypy) rather than a runtime one.
    """
    meta = getattr(origin_type, "__pydantic_generic_metadata__", None)
    if not meta:
        return None
    base = meta.get("origin")
    if base is None:
        return None

    def _check(
        value: Any,
        _origin_type: Any,
        _args: tuple[Any, ...],
        _memo: TypeCheckMemo,
    ) -> None:
        if not isinstance(value, base):
            msg = f"is not an instance of {base.__qualname__}"
            raise TypeCheckError(msg)

    return _check


def _mocked_annotation_lookup(
    origin_type: Any,
    args: tuple[Any, ...],
    extras: tuple[Any, ...],
) -> TypeCheckerCallable | None:
    """Skip the check when the annotation type itself is a ``Mock``.

    A test that patches a class used in a typeguard-instrumented annotation
    (e.g. ``patch("...AsyncConnectionPool")`` against ``pool: AsyncConnectionPool
    | None``) makes typeguard evaluate that annotation to a ``Mock`` at runtime,
    which then misroutes to ``check_protocol`` (a ``Mock``'s ``_is_protocol`` is
    truthy) and raises ``TypeError: ... is not a Protocol``. typeguard already
    skips when the VALUE is a ``Mock``; this is the symmetric case for the
    annotation TYPE, where there is nothing meaningful to check against.

    The skip emits a ``TypeHintWarning`` (filtered to ``default`` in pyproject)
    so it stays visible in the pytest warnings summary, keeping the unchecked
    surface countable like the forward-ref WARN path rather than going dark.
    """
    if isinstance(origin_type, Mock):

        def _skip(
            value: Any,
            _origin_type: Any,
            _args: tuple[Any, ...],
            _memo: TypeCheckMemo,
        ) -> None:
            warnings.warn(
                "Skipping type check: the annotation type resolved to a Mock "
                f"({origin_type!r}); a patch is suppressing instrumentation at "
                "this boundary.",
                TypeHintWarning,
                stacklevel=2,
            )

        return _skip
    return None


_registered = False


def register_policy_honoring_checker() -> None:
    """Install the WARN-activation checker extensions at the front of the chain.

    Registers three lookups: the NameError-tolerant wrapper (eager-eval
    ``TYPE_CHECKING``-only signatures), the unbound-pydantic-generic relaxation,
    and the mocked-annotation skip (a patched annotation type that resolves to a
    ``Mock``). Idempotent: a repeat call is a no-op, so re-importing this module
    (e.g. each xdist worker re-running conftest) does not stack duplicate lookups.
    """
    global _registered  # noqa: PLW0603 -- module-level one-shot guard
    if _registered:
        return
    # Each ``insert(0, ...)`` prepends, so the LAST inserted runs FIRST.
    # ``_mocked_annotation_lookup`` must run before the others: a Mock's
    # ``_is_protocol`` is truthy, so the builtin (delegated to by
    # ``_policy_honoring_lookup``) would route a mocked annotation type to
    # ``check_protocol`` and raise a ``TypeError`` the NameError wrapper does not
    # catch.
    typeguard.checker_lookup_functions.insert(0, _pydantic_generic_lookup)
    typeguard.checker_lookup_functions.insert(0, _policy_honoring_lookup)
    typeguard.checker_lookup_functions.insert(0, _mocked_annotation_lookup)
    _registered = True
