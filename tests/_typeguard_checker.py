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

import typeguard
from typeguard import (
    ForwardRefPolicy,
    TypeCheckerCallable,
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


_registered = False


def register_policy_honoring_checker() -> None:
    """Install the policy-honouring lookup at the front of the checker chain.

    Idempotent: a repeat call is a no-op, so re-importing this module (e.g. each
    xdist worker re-running conftest) does not stack duplicate wrappers.
    """
    global _registered  # noqa: PLW0603 -- module-level one-shot guard
    if _registered:
        return
    typeguard.checker_lookup_functions.insert(0, _policy_honoring_lookup)
    _registered = True
