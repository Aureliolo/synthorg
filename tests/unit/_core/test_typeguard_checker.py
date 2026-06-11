"""Tests for the policy-honouring typeguard checker.

Verifies that a check-time ``NameError`` (raised when a structural checker
eagerly evaluates a ``TYPE_CHECKING``-only signature member under PEP 649) is
routed through ``memo.config.forward_ref_policy`` rather than escaping as a raw
test error, while genuine resolved-type mismatches still raise
``TypeCheckError``. See ``tests/_typeguard_checker.py`` for the mechanism.
"""

import warnings
from datetime import UTC, datetime
from typing import TYPE_CHECKING, NamedTuple, Protocol, cast, runtime_checkable
from unittest.mock import Mock

if TYPE_CHECKING:
    # A name visible ONLY to static analysis, absent at runtime: exactly the
    # TYPE_CHECKING-guarded signature type the checker exists to tolerate. Used
    # below as a NamedTuple field type so accessing ``__annotations__`` under
    # PEP 649 raises the eager-eval NameError at check time.
    from datetime import datetime as _runtime_unbound

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
    _wrap,
    register_policy_honoring_checker,
)

pytestmark = pytest.mark.unit


def _memo(policy: ForwardRefPolicy) -> TypeCheckMemo:
    """Build a real ``TypeCheckMemo`` carrying the given forward-ref policy."""
    return TypeCheckMemo(
        globals={},
        locals={},
        config=TypeCheckConfiguration(forward_ref_policy=policy),
    )


def _raise_name_error(
    value: object,
    origin_type: object,
    args: tuple[object, ...],
    memo: TypeCheckMemo,
) -> None:
    """Stand-in inner checker that fails exactly as the eager-eval path does."""
    raise NameError(name="UnresolvableName")


class TestWrap:
    """Unit-level behaviour of ``_wrap`` against each forward-ref policy."""

    def test_warn_emits_warning_and_skips(self) -> None:
        wrapped = _wrap(_raise_name_error)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = wrapped(object(), object, (), _memo(ForwardRefPolicy.WARN))
        assert result is None
        assert any(issubclass(w.category, TypeHintWarning) for w in caught)
        assert any("UnresolvableName" in str(w.message) for w in caught)

    def test_ignore_skips_silently(self) -> None:
        wrapped = _wrap(_raise_name_error)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = wrapped(object(), object, (), _memo(ForwardRefPolicy.IGNORE))
        assert result is None
        assert not caught

    def test_error_reraises(self) -> None:
        wrapped = _wrap(_raise_name_error)
        with pytest.raises(NameError):
            wrapped(object(), object, (), _memo(ForwardRefPolicy.ERROR))

    def test_non_nameerror_propagates(self) -> None:
        def _raise_type_check(
            value: object,
            origin_type: object,
            args: tuple[object, ...],
            memo: TypeCheckMemo,
        ) -> None:
            msg = "is not compatible"
            raise TypeCheckError(msg)

        wrapped = _wrap(_raise_type_check)
        with pytest.raises(TypeCheckError):
            wrapped(object(), object, (), _memo(ForwardRefPolicy.WARN))


class _BadTuple(NamedTuple):
    """NamedTuple whose field type has no runtime binding.

    ``check_tuple`` reads ``origin_type.__annotations__`` before any structural
    check; under PEP 649 that evaluation raises ``NameError`` on the unresolved
    field type, exercising the eager-eval path the checker exists to police.
    """

    field: _runtime_unbound


@runtime_checkable
class _ProtoResolvable(Protocol):
    """Fully-resolvable Protocol used to prove real mismatches still raise."""

    def act(self) -> None: ...


class TestIntegration:
    """End-to-end behaviour through ``typeguard.check_type`` with the lookup live."""

    def test_warn_passes_unresolved_member(self) -> None:
        register_policy_honoring_checker()  # idempotent; conftest also registers
        value = _BadTuple(field=datetime(2026, 1, 1, tzinfo=UTC))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            # Without the checker this raises a raw NameError; with it, WARN
            # warns-and-skips so the check completes without error.
            check_type(value, _BadTuple, forward_ref_policy=ForwardRefPolicy.WARN)
        assert any(issubclass(w.category, TypeHintWarning) for w in caught)

    def test_ignore_passes_unresolved_member(self) -> None:
        register_policy_honoring_checker()
        value = _BadTuple(field=datetime(2026, 1, 1, tzinfo=UTC))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            # IGNORE skips the unresolved member silently: the check completes
            # without error AND without a TypeHintWarning.
            check_type(value, _BadTuple, forward_ref_policy=ForwardRefPolicy.IGNORE)
        assert not any(issubclass(w.category, TypeHintWarning) for w in caught)

    def test_error_reraises_unresolved_member(self) -> None:
        register_policy_honoring_checker()
        value = _BadTuple(field=datetime(2026, 1, 1, tzinfo=UTC))
        with pytest.raises(NameError):
            check_type(value, _BadTuple, forward_ref_policy=ForwardRefPolicy.ERROR)

    def test_real_mismatch_still_raises(self) -> None:
        register_policy_honoring_checker()
        with pytest.raises(TypeCheckError):
            check_type(
                object(),
                _ProtoResolvable,
                forward_ref_policy=ForwardRefPolicy.WARN,
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
        register_policy_honoring_checker()
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
        register_policy_honoring_checker()
        # A bare instance (constructed without binding the parameter) passes a
        # concrete ``Model[Concrete]`` annotation: pydantic erases the type
        # argument on bare construction, so the runtime check is against the base.
        check_type(_Snapshot(payload=_Payload()), _Snapshot[_Payload])

    def test_non_instance_still_rejected(self) -> None:
        register_policy_honoring_checker()
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
            register_policy_honoring_checker()
            after_first = len(typeguard.checker_lookup_functions)
            register_policy_honoring_checker()
            after_second = len(typeguard.checker_lookup_functions)
            assert after_second == after_first
        finally:
            typeguard.checker_lookup_functions[:] = original
            tgc._registered = original_flag
