"""Typeguard checker extensions for boundaries unverifiable at runtime.

typeguard 4.5.2's structural checkers (``check_protocol``, ``check_callable``,
``check_tuple``, ``check_typed_dict``) eagerly evaluate member / signature
annotations via ``inspect.signature`` / ``typing.get_type_hints`` /
``__annotations__``. Under the ERROR forward-ref policy every instrumented
signature or member type must resolve at runtime, so an unresolved name fails
the test. A handful of boundaries cannot be checked at runtime by construction,
and ``register_typeguard_checker_extensions`` installs a lookup at the front of
``typeguard.checker_lookup_functions`` for each:

- ``_litestar_scope_lookup``: litestar's ASGI ``Scope`` TypedDicts declare
  ``app: Litestar`` under litestar's own ``if TYPE_CHECKING:`` guard, which no
  ``synthorg``-side hoist can resolve; skip the structural check.
- ``_mocked_annotation_lookup``: a patched annotation type that resolves to a
  ``Mock`` has nothing meaningful to check against; skip it.
- ``_pydantic_discriminated_union_lookup``: typeguard cannot evaluate a pydantic
  discriminated union (e.g. ``JsonValue``); skip it (pydantic validates at the
  model boundary).
- ``_pydantic_generic_lookup``: relax a bare ``Model[X]`` instance to its origin
  base class, which is the honest runtime check once the type argument is erased.

The first three emit a ``TypeHintWarning`` so the unchecked surface stays
countable in the pytest warnings summary. The module imports only ``typeguard``
(no ``synthorg``) so the conftest can register these extensions before installing
the import hook, without pulling any ``synthorg`` module into the interpreter
ahead of instrumentation.
"""

import warnings
from types import UnionType
from typing import Union
from unittest.mock import Mock

import typeguard
from pydantic import Discriminator
from typeguard import (
    TypeCheckerCallable,
    TypeCheckError,
    TypeCheckMemo,
    TypeHintWarning,
)


def _pydantic_generic_lookup(
    origin_type: object,
    args: tuple[object, ...],
    extras: tuple[object, ...],
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
        value: object,
        _origin_type: object,
        _args: tuple[object, ...],
        _memo: TypeCheckMemo,
    ) -> None:
        if not isinstance(value, base):
            msg = f"is not an instance of {base.__qualname__}"
            raise TypeCheckError(msg)

    return _check


_UNION_ORIGINS = frozenset({Union, UnionType})


def _pydantic_discriminated_union_lookup(
    origin_type: object,
    args: tuple[object, ...],
    extras: tuple[object, ...],
) -> TypeCheckerCallable | None:
    """Skip checks for a pydantic discriminated union (e.g. ``JsonValue``).

    pydantic encodes member selection for a ``Discriminator``-tagged union in a
    runtime callable typeguard never executes; typeguard instead tries each
    member structurally and rejects values the discriminator would accept (a
    nested ``JsonValue`` dict fails every flat member, for instance). typeguard
    cannot faithfully evaluate such a union, so skip it: pydantic still
    validates the real value at the model boundary via the field.

    The skip emits a ``TypeHintWarning`` so the unchecked surface stays
    countable, like the forward-ref WARN path.
    """
    if origin_type in _UNION_ORIGINS and any(
        isinstance(extra, Discriminator) for extra in extras
    ):

        def _skip(
            value: object,
            _origin_type: object,
            _args: tuple[object, ...],
            _memo: TypeCheckMemo,
        ) -> None:
            warnings.warn(
                "Skipping type check: a pydantic discriminated union "
                "(e.g. JsonValue) cannot be evaluated at runtime; pydantic "
                "validates the value at the model boundary.",
                TypeHintWarning,
                stacklevel=2,
            )

        return _skip
    return None


def _mocked_annotation_lookup(
    origin_type: object,
    args: tuple[object, ...],
    extras: tuple[object, ...],
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
            value: object,
            _origin_type: object,
            _args: tuple[object, ...],
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


_LITESTAR_SCOPE_TYPED_DICTS = frozenset({"HTTPScope", "WebSocketScope", "BaseScope"})


def _litestar_scope_lookup(
    origin_type: object,
    args: tuple[object, ...],
    extras: tuple[object, ...],
) -> TypeCheckerCallable | None:
    """Skip checks for litestar's ASGI ``Scope`` TypedDicts.

    litestar's ``HTTPScope`` / ``WebSocketScope`` (and their ``BaseScope``)
    declare ``app: Litestar`` and ``litestar_app: Litestar`` under litestar's own
    ``if TYPE_CHECKING:`` block (``litestar.types.asgi_types``). Any synthorg code
    annotating ``scope: Scope`` (the ASGI drain middleware, raw-ASGI handlers)
    makes typeguard's ``check_typed_dict`` eagerly evaluate those members, raising
    a raw ``NameError: Litestar`` that no synthorg-side hoist can fix (the guard
    lives in the third-party package). Skip the structural check for these
    TypedDicts; the ASGI value is a plain ``dict`` litestar itself validates.

    Matched by name + module rather than importing litestar, so this module stays
    free of any import that would run before the typeguard import hook installs.
    The skip emits a ``TypeHintWarning`` so the unchecked surface stays countable,
    like the forward-ref WARN path.
    """
    if (
        getattr(origin_type, "__name__", "") in _LITESTAR_SCOPE_TYPED_DICTS
        and getattr(origin_type, "__module__", "") == "litestar.types.asgi_types"
    ):

        def _skip(
            value: object,
            _origin_type: object,
            _args: tuple[object, ...],
            _memo: TypeCheckMemo,
        ) -> None:
            warnings.warn(
                "Skipping type check: litestar's ASGI Scope TypedDict declares "
                "'app: Litestar' under litestar's own TYPE_CHECKING guard, which "
                "cannot be resolved at runtime; litestar validates the ASGI "
                "scope dict itself.",
                TypeHintWarning,
                stacklevel=2,
            )

        return _skip
    return None


_registered = False


def register_typeguard_checker_extensions() -> None:
    """Install the checker extensions at the front of the lookup chain.

    Registers four lookups: the litestar Scope TypedDict skip (third-party
    ``TYPE_CHECKING``-guarded members), the unbound-pydantic-generic relaxation,
    the mocked-annotation skip (a patched annotation type that resolves to a
    ``Mock``), and the pydantic discriminated-union skip (e.g. ``JsonValue``).
    Idempotent: a repeat call is a no-op, so re-importing this module (e.g. each
    xdist worker re-running conftest) does not stack duplicate lookups.
    """
    global _registered  # noqa: PLW0603 -- module-level one-shot guard
    if _registered:
        return
    # Each ``insert(0, ...)`` prepends, so the checker inserted LAST runs FIRST.
    # ``_mocked_annotation_lookup`` must run before typeguard's builtin lookup:
    # a Mock's ``_is_protocol`` is truthy, so the builtin would route a mocked
    # annotation type to ``check_protocol`` and raise a ``TypeError``.
    typeguard.checker_lookup_functions.insert(0, _pydantic_generic_lookup)
    typeguard.checker_lookup_functions.insert(0, _pydantic_discriminated_union_lookup)
    typeguard.checker_lookup_functions.insert(0, _mocked_annotation_lookup)
    # Inserted last, so it runs FIRST of all the lookups: litestar's ASGI
    # ``Scope`` TypedDicts must be skipped before the builtin ``check_typed_dict``
    # eagerly evals their members and raises ``NameError: Litestar`` from the
    # third-party guard.
    typeguard.checker_lookup_functions.insert(0, _litestar_scope_lookup)
    _registered = True
