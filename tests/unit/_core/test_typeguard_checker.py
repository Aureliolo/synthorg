"""Tests for the policy-honouring typeguard checker.

Verifies that a check-time ``NameError`` (raised when a structural checker
eagerly evaluates a ``TYPE_CHECKING``-only signature member under PEP 649) is
routed through ``memo.config.forward_ref_policy`` rather than escaping as a raw
test error, while genuine resolved-type mismatches still raise
``TypeCheckError``. See ``tests/_typeguard_checker.py`` for the mechanism.
"""

import warnings
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, NamedTuple, Protocol, cast, runtime_checkable

if TYPE_CHECKING:
    # A name visible ONLY to static analysis, absent at runtime: exactly the
    # TYPE_CHECKING-guarded signature type the checker exists to tolerate. Used
    # below as a NamedTuple field type so accessing ``__annotations__`` under
    # PEP 649 raises the eager-eval NameError at check time.
    from datetime import datetime as _runtime_unbound

import pytest
from pydantic import BaseModel
from typeguard import (
    ForwardRefPolicy,
    TypeCheckConfiguration,
    TypeCheckError,
    TypeCheckMemo,
    TypeHintWarning,
    check_type,
)

from tests._typeguard_checker import _wrap, register_policy_honoring_checker

pytestmark = pytest.mark.unit


def _memo(policy: ForwardRefPolicy) -> TypeCheckMemo:
    """Build a real ``TypeCheckMemo`` carrying the given forward-ref policy."""
    return TypeCheckMemo(
        globals={},
        locals={},
        config=TypeCheckConfiguration(forward_ref_policy=policy),
    )


def _raise_name_error(
    value: Any,
    origin_type: Any,
    args: tuple[Any, ...],
    memo: TypeCheckMemo,
) -> Any:
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
            value: Any,
            origin_type: Any,
            args: tuple[Any, ...],
            memo: TypeCheckMemo,
        ) -> Any:
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

    payload: U


class _Payload(BaseModel):
    value: int = 0


class TestUnboundPydanticGeneric:
    """A bare pydantic-generic instance satisfies the ``Model[T]`` annotation."""

    def test_unbound_typevar_relaxes_to_base(self) -> None:
        register_policy_honoring_checker()
        # Parameterize by the class's own free TypeVar, as a generic repository
        # annotates ``Model[T]`` with its unbound parameter. Built via
        # ``__class_getitem__`` so the free TypeVar is not analysed as a type.
        unbound = _Snapshot.__class_getitem__(cast("Any", _Snapshot.__type_params__[0]))
        bare = _Snapshot(payload=_Payload())  # constructed as the base, not Snapshot[T]
        # Without the relaxation typeguard raises (bare is not an instance of the
        # pydantic-built Snapshot[U] subclass); with it the base check passes.
        check_type(bare, unbound)

    def test_concrete_parameterization_still_checked(self) -> None:
        register_policy_honoring_checker()
        with pytest.raises(TypeCheckError):
            check_type("not a snapshot", _Snapshot[_Payload])
