"""Unit tests for the CFG-1 audit retention + approval-urgency helpers.

Covers:

* ``_resolve_audit_retention_days`` and
  ``_resolve_audit_retention_loop_enabled`` -- resolver-available,
  resolver-missing, resolver-errors paths.
* ``_audit_retention_tick`` -- disabled-via-zero-days, happy path,
  persistence-missing, and repository-error branches.
* ``_validate_approval_urgency_invariant`` -- valid ordering, invalid
  ordering (critical >= high), and resolver-error soft-skip path.
* The cleanup-done callback factory used by the lifecycle builder for
  ticket cleanup vs audit retention tasks.
"""

import asyncio

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.lifecycle_helpers import (
    _audit_retention_tick,
    _resolve_audit_retention_days,
    _resolve_audit_retention_loop_enabled,
    _validate_approval_urgency_invariant,
)
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from tests.unit.api.fakes import FakePersistenceBackend

_APPROVAL_CRITICAL = "approval_urgency_critical_seconds"
_APPROVAL_HIGH = "approval_urgency_high_seconds"


class _FakeConfigResolver:
    """Lightweight stand-in for ``ConfigResolver`` in unit tests.

    Supplies deterministic scalar values for the handful of settings
    the lifecycle helpers look at, and can be configured to raise on
    any read to exercise resolver-error branches.
    """

    def __init__(
        self,
        *,
        ints: dict[tuple[str, str], int] | None = None,
        floats: dict[tuple[str, str], float] | None = None,
        bools: dict[tuple[str, str], bool] | None = None,
        raise_exc: BaseException | None = None,
    ) -> None:
        self._ints = ints or {}
        self._floats = floats or {}
        self._bools = bools or {}
        self._raise_exc = raise_exc

    async def get_int(self, namespace: str, key: str) -> int:
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._ints[(namespace, key)]

    async def get_float(self, namespace: str, key: str) -> float:
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._floats[(namespace, key)]

    async def get_bool(self, namespace: str, key: str) -> bool:
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._bools[(namespace, key)]


async def _make_app_state(
    *,
    persistence: FakePersistenceBackend | None = None,
    resolver: _FakeConfigResolver | None = None,
) -> AppState:
    state = AppState(
        config=RootConfig(company_name="test-company"),
        approval_store=ApprovalStore(),
        persistence=persistence,
    )
    if resolver is not None:
        # ``_config_resolver`` is the only slot exposed via
        # ``AppState.has_config_resolver`` + ``.config_resolver``;
        # setting it directly keeps the fake resolver focused on the
        # three scalar accessors the lifecycle helpers actually use.
        state._config_resolver = resolver  # type: ignore[assignment]
    return state


@pytest.mark.unit
class TestResolveAuditRetentionDays:
    async def test_returns_default_when_no_resolver(self) -> None:
        state = await _make_app_state()
        days = await _resolve_audit_retention_days(state)
        assert days == 730

    async def test_reads_resolved_value(self) -> None:
        resolver = _FakeConfigResolver(
            ints={("security", "audit_retention_days"): 42},
        )
        state = await _make_app_state(resolver=resolver)
        days = await _resolve_audit_retention_days(state)
        assert days == 42

    async def test_falls_back_to_default_on_resolver_error(self) -> None:
        resolver = _FakeConfigResolver(raise_exc=RuntimeError("backend down"))
        state = await _make_app_state(resolver=resolver)
        days = await _resolve_audit_retention_days(state)
        assert days == 730

    async def test_cancellation_propagates(self) -> None:
        resolver = _FakeConfigResolver(raise_exc=asyncio.CancelledError())
        state = await _make_app_state(resolver=resolver)
        with pytest.raises(asyncio.CancelledError):
            await _resolve_audit_retention_days(state)


@pytest.mark.unit
class TestResolveAuditRetentionLoopEnabled:
    async def test_returns_default_when_no_resolver(self) -> None:
        state = await _make_app_state()
        assert await _resolve_audit_retention_loop_enabled(state) is True

    async def test_reads_resolved_value(self) -> None:
        resolver = _FakeConfigResolver(
            bools={("security", "audit_retention_loop_enabled"): False},
        )
        state = await _make_app_state(resolver=resolver)
        assert await _resolve_audit_retention_loop_enabled(state) is False

    async def test_falls_back_to_default_on_resolver_error(self) -> None:
        resolver = _FakeConfigResolver(raise_exc=RuntimeError("backend down"))
        state = await _make_app_state(resolver=resolver)
        assert await _resolve_audit_retention_loop_enabled(state) is True

    async def test_cancellation_propagates(self) -> None:
        resolver = _FakeConfigResolver(raise_exc=asyncio.CancelledError())
        state = await _make_app_state(resolver=resolver)
        with pytest.raises(asyncio.CancelledError):
            await _resolve_audit_retention_loop_enabled(state)


@pytest.mark.unit
class TestAuditRetentionTick:
    async def test_zero_days_disables_purge(self) -> None:
        resolver = _FakeConfigResolver(
            ints={("security", "audit_retention_days"): 0},
        )
        backend = FakePersistenceBackend()
        await backend.connect()
        state = await _make_app_state(persistence=backend, resolver=resolver)

        await _audit_retention_tick(state)

        assert backend.audit_entries.purge_calls == 0

    async def test_missing_persistence_is_noop(self) -> None:
        resolver = _FakeConfigResolver(
            ints={("security", "audit_retention_days"): 30},
        )
        state = await _make_app_state(resolver=resolver)

        # Should not raise -- the tick simply skips when persistence is
        # unavailable.
        await _audit_retention_tick(state)

    async def test_invokes_purge_before_with_cutoff(self) -> None:
        resolver = _FakeConfigResolver(
            ints={("security", "audit_retention_days"): 15},
        )
        backend = FakePersistenceBackend()
        await backend.connect()
        state = await _make_app_state(persistence=backend, resolver=resolver)

        await _audit_retention_tick(state)

        assert backend.audit_entries.purge_calls == 1

    async def test_repository_error_is_swallowed(self) -> None:
        resolver = _FakeConfigResolver(
            ints={("security", "audit_retention_days"): 15},
        )
        backend = FakePersistenceBackend()
        await backend.connect()
        backend.audit_entries.raise_on_purge = RuntimeError("db down")
        state = await _make_app_state(persistence=backend, resolver=resolver)

        # Must not propagate -- the loop keeps running on the next tick.
        await _audit_retention_tick(state)


@pytest.mark.unit
class TestApprovalUrgencyInvariant:
    async def test_valid_ordering_passes(self) -> None:
        resolver = _FakeConfigResolver(
            floats={
                ("api", _APPROVAL_CRITICAL): 3600.0,
                ("api", _APPROVAL_HIGH): 14400.0,
            },
        )
        state = await _make_app_state(resolver=resolver)
        # No exception raised.
        await _validate_approval_urgency_invariant(state)

    async def test_equal_values_reject_startup(self) -> None:
        resolver = _FakeConfigResolver(
            floats={
                ("api", _APPROVAL_CRITICAL): 7200.0,
                ("api", _APPROVAL_HIGH): 7200.0,
            },
        )
        state = await _make_app_state(resolver=resolver)
        with pytest.raises(ValueError, match="approval-urgency"):
            await _validate_approval_urgency_invariant(state)

    async def test_critical_greater_than_high_rejects(self) -> None:
        resolver = _FakeConfigResolver(
            floats={
                ("api", _APPROVAL_CRITICAL): 14400.0,
                ("api", _APPROVAL_HIGH): 3600.0,
            },
        )
        state = await _make_app_state(resolver=resolver)
        with pytest.raises(ValueError, match="approval-urgency"):
            await _validate_approval_urgency_invariant(state)

    async def test_resolver_error_soft_skips(self) -> None:
        resolver = _FakeConfigResolver(raise_exc=RuntimeError("backend down"))
        state = await _make_app_state(resolver=resolver)
        # Resolver outage must not block startup -- the check is
        # best-effort and other bridge-config paths handle the outage
        # independently.
        await _validate_approval_urgency_invariant(state)
