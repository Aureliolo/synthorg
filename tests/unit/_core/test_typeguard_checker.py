"""Tests for the typeguard checker extensions.

Verifies the lookups in ``tests/_typeguard_checker.py`` that handle boundaries
typeguard cannot check at runtime by construction: the unbound-pydantic-generic
relaxation and the mocked-annotation skip, plus the idempotent registration that
installs them. See ``tests/_typeguard_checker.py`` for the mechanism.
"""

import warnings
from typing import cast
from unittest.mock import Mock

import pytest
import typeguard
from pydantic import BaseModel, ConfigDict
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
    _mocked_annotation_lookup,
    register_typeguard_checker_extensions,
)

pytestmark = pytest.mark.unit


def _memo(policy: ForwardRefPolicy) -> TypeCheckMemo:
    """Build a real ``TypeCheckMemo`` carrying the given forward-ref policy."""
    return TypeCheckMemo(
        globals={},
        locals={},
        config=TypeCheckConfiguration(forward_ref_policy=policy),
    )


class _Snapshot[U: BaseModel](BaseModel):
    """Pydantic generic mirroring the VersionSnapshot[T] repository pattern."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    payload: U


class _Payload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

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
            result = checker(object(), object, (), _memo(ForwardRefPolicy.WARN))
        assert result is None
        assert any(issubclass(w.category, TypeHintWarning) for w in caught)

    def test_non_mock_annotation_falls_through(self) -> None:
        # A real type yields no special checker, so typeguard's own dispatch runs.
        assert _mocked_annotation_lookup(int, (), ()) is None


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
            register_typeguard_checker_extensions()
            after_second = len(typeguard.checker_lookup_functions)
            assert after_second == after_first
        finally:
            typeguard.checker_lookup_functions[:] = original
            tgc._registered = original_flag
