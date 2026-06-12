"""Tests for the typeguard checker extensions.

Verifies the four lookups in ``tests/_typeguard_checker.py`` that handle
boundaries typeguard cannot check at runtime by construction (the
unbound-pydantic-generic relaxation, the mocked-annotation skip, the pydantic
discriminated-union skip, and the litestar Scope TypedDict skip), plus the
idempotent registration that installs them. See ``tests/_typeguard_checker.py``
for the mechanism.
"""

import warnings
from typing import Final, Union, cast
from unittest.mock import Mock

import pytest
import typeguard
from pydantic import BaseModel, ConfigDict, Discriminator
from typeguard import (
    ForwardRefPolicy,
    TypeCheckConfiguration,
    TypeCheckError,
    TypeCheckMemo,
    TypeHintWarning,
    check_type,
)

from tests import _typeguard_checker as tgc
from tests._typeguard_checker import (
    _litestar_scope_lookup,
    _mocked_annotation_lookup,
    _pydantic_discriminated_union_lookup,
    register_typeguard_checker_extensions,
)

pytestmark = pytest.mark.unit

# register_typeguard_checker_extensions installs exactly these four lookups.
_EXTENSION_LOOKUP_COUNT: Final = 4


def _memo(policy: ForwardRefPolicy) -> TypeCheckMemo:
    """Build a real ``TypeCheckMemo`` carrying the given forward-ref policy."""
    return TypeCheckMemo(
        globals={},
        locals={},
        config=TypeCheckConfiguration(forward_ref_policy=policy),
    )


class _Snapshot[U: BaseModel](BaseModel):
    """Pydantic generic mirroring the VersionSnapshot[T] repository pattern."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    payload: U


class _Payload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    value: int = 0


class TestUnboundPydanticGeneric:
    """A bare pydantic-generic instance satisfies the ``Model[T]`` annotation."""

    def test_unbound_typevar_relaxes_to_base(self) -> None:
        register_typeguard_checker_extensions()
        # Parameterize by the class's own free TypeVar, as a generic repository
        # annotates ``Model[T]`` with its unbound parameter. Built via
        # ``__class_getitem__`` so the free TypeVar is not analysed as a type.
        unbound = _Snapshot.__class_getitem__(
            cast("type", _Snapshot.__type_params__[0])
        )
        bare = _Snapshot(payload=_Payload())  # constructed as the base, not Snapshot[T]
        # Without the relaxation typeguard raises (bare is not an instance of the
        # pydantic-built Snapshot[U] subclass); with it the base check passes.
        check_type(bare, unbound)

    def test_concrete_parameterization_relaxes_to_base(self) -> None:
        register_typeguard_checker_extensions()
        # A bare instance (constructed without binding the parameter) passes a
        # concrete ``Model[Concrete]`` annotation: pydantic erases the type
        # argument on bare construction, so the runtime check is against the base.
        check_type(_Snapshot(payload=_Payload()), _Snapshot[_Payload])

    def test_non_instance_still_rejected(self) -> None:
        register_typeguard_checker_extensions()
        # The relaxation is to the ORIGIN base, not to ``Any``: a value that is
        # not even a base-class instance still raises.
        with pytest.raises(TypeCheckError):
            check_type("not a snapshot", _Snapshot[_Payload])


class TestMockedAnnotation:
    """``_mocked_annotation_lookup`` skips (and warns) when the TYPE is a Mock.

    This is the load-bearing first-in-chain lookup: a ``Mock``'s ``_is_protocol``
    is truthy, so without it the builtin would route a mocked annotation type to
    ``check_protocol`` and raise ``TypeError``.
    """

    def test_mock_annotation_returns_skipping_checker(self) -> None:
        # ``Mock(spec=type)`` stands in for a patched class object: still a
        # ``Mock`` instance (so the lookup fires), with a spec for the ratchet.
        checker = _mocked_annotation_lookup(Mock(spec=type), (), ())
        assert checker is not None
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = checker(object(), object, (), _memo(ForwardRefPolicy.ERROR))
        assert result is None
        assert any(issubclass(w.category, TypeHintWarning) for w in caught)

    def test_non_mock_annotation_falls_through(self) -> None:
        # A real type yields no special checker, so typeguard's own dispatch runs.
        assert _mocked_annotation_lookup(int, (), ()) is None


class TestDiscriminatedUnion:
    """``_pydantic_discriminated_union_lookup`` skips a Discriminator-tagged union.

    typeguard cannot evaluate a pydantic discriminated union (it tries each
    member structurally and rejects values the discriminator would accept), so
    the lookup skips it; pydantic validates the value at the model boundary.
    """

    def test_discriminated_union_returns_skipping_checker(self) -> None:
        # A union origin whose extras carry a Discriminator is skipped-and-warned.
        checker = _pydantic_discriminated_union_lookup(
            Union, (), (Discriminator("kind"),)
        )
        assert checker is not None
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = checker(object(), object, (), _memo(ForwardRefPolicy.ERROR))
        assert result is None
        assert any(issubclass(w.category, TypeHintWarning) for w in caught)

    def test_union_without_discriminator_falls_through(self) -> None:
        # A plain union (no Discriminator in extras) is not a discriminated union.
        assert _pydantic_discriminated_union_lookup(Union, (), ()) is None

    def test_non_union_origin_falls_through(self) -> None:
        # A Discriminator on a non-union origin is not matched.
        checker = _pydantic_discriminated_union_lookup(int, (), (Discriminator("k"),))
        assert checker is None


class TestLitestarScope:
    """``_litestar_scope_lookup`` skips litestar's ASGI Scope TypedDicts.

    The lookup matches by ``__name__`` + ``__module__`` strings rather than
    importing litestar (so the module stays import-free before the typeguard
    hook installs); these tests pin that name+module contract.
    """

    def test_litestar_scope_returns_skipping_checker(self) -> None:
        # A stand-in matching litestar's HTTPScope by __name__ AND __module__.
        scope = type("HTTPScope", (dict,), {})
        scope.__module__ = "litestar.types.asgi_types"
        checker = _litestar_scope_lookup(scope, (), ())
        assert checker is not None
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = checker(object(), object, (), _memo(ForwardRefPolicy.ERROR))
        assert result is None
        assert any(issubclass(w.category, TypeHintWarning) for w in caught)

    def test_matching_name_wrong_module_falls_through(self) -> None:
        # Same TypedDict name but a non-litestar module is NOT a litestar Scope.
        impostor = type("HTTPScope", (dict,), {})
        impostor.__module__ = "synthorg.not_litestar"
        assert _litestar_scope_lookup(impostor, (), ()) is None

    def test_unrelated_name_falls_through(self) -> None:
        # A litestar-module type whose name is not a known Scope TypedDict.
        other = type("Request", (dict,), {})
        other.__module__ = "litestar.types.asgi_types"
        assert _litestar_scope_lookup(other, (), ()) is None


class TestRegistration:
    """Registration is idempotent across repeated calls (xdist conftest re-runs)."""

    def test_register_does_not_stack_lookups(self) -> None:
        original = list(typeguard.checker_lookup_functions)
        original_flag = tgc._registered
        try:
            # Force a fresh registration, then confirm a second call is a no-op
            # (the guard prevents duplicate lookups on every worker re-import).
            tgc._registered = False
            register_typeguard_checker_extensions()
            after_first = len(typeguard.checker_lookup_functions)
            # The forced re-registration prepends exactly the four extension
            # lookups (conftest already registered an identical set at import).
            assert after_first == len(original) + _EXTENSION_LOOKUP_COUNT
            register_typeguard_checker_extensions()
            after_second = len(typeguard.checker_lookup_functions)
            assert after_second == after_first
        finally:
            typeguard.checker_lookup_functions[:] = original
            tgc._registered = original_flag
