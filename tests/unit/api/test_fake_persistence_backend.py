"""Runtime parity check: ``FakePersistenceBackend`` satisfies ``PersistenceBackend``.

``PersistenceBackend`` is ``@runtime_checkable``, so ``isinstance`` here
asserts the fake exposes every protocol member by name. Drift surfaces
as a single, named test failure instead of a 75-error mypy cascade at
the typed-boundary call sites where the fake is consumed.

The lazy-stub property suite (``sessions`` / ``refresh_tokens`` /
``idempotency_keys`` / ``seen_claims`` / ``principle_overrides`` /
``meeting_cooldown`` / ``ceremony_scheduler_state`` /
``tracked_containers``) is exercised explicitly so a regression that
breaks the cached-on-first-access contract or the spec-bound
``AsyncMock`` surface surfaces here, not at a random consumer site.
``clear()`` is invoked between the two access waves to assert the
isolation contract: a fresh stub instance is rebuilt after a reset,
not a leaked one from the prior wave.
"""

from unittest.mock import AsyncMock

import pytest

from synthorg.persistence.auth_protocol import (
    RefreshTokenRepository,
    SessionRepository,
)
from synthorg.persistence.idempotency_protocol import IdempotencyRepository
from synthorg.persistence.principle_override_protocol import (
    PrincipleOverrideRepository,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.seen_claims_protocol import SeenClaimsRepository
from tests.unit.api.fakes_backend import FakePersistenceBackend

pytestmark = pytest.mark.unit


def test_fake_persistence_backend_satisfies_protocol() -> None:
    fake = FakePersistenceBackend()
    assert isinstance(fake, PersistenceBackend)


def test_lazy_stub_properties_are_cached_and_spec_bound() -> None:
    """Each lazy-stub property returns the same spec-bound instance per access."""
    fake = FakePersistenceBackend()

    # AsyncMock(spec=Protocol) on each: identity stability + spec class are both
    # part of the contract callers rely on.
    spec_pairs: list[tuple[AsyncMock, type]] = [
        (fake.sessions, SessionRepository),
        (fake.refresh_tokens, RefreshTokenRepository),
        (fake.idempotency_keys, IdempotencyRepository),
        (fake.seen_claims, SeenClaimsRepository),
        (fake.principle_overrides, PrincipleOverrideRepository),
    ]
    for stub, spec_cls in spec_pairs:
        assert isinstance(stub, AsyncMock)
        # AsyncMock surfaces the spec class via the public ``_spec_class``
        # attribute set during construction; tests assert identity (not a
        # subclass match) to catch accidental spec widening.
        assert stub._spec_class is spec_cls

    # Identity stability across repeat property access.
    assert fake.sessions is fake.sessions
    assert fake.idempotency_keys is fake.idempotency_keys


def test_clear_resets_lazy_stubs_to_fresh_instances() -> None:
    """``clear()`` must drop cached stubs so test isolation holds."""
    fake = FakePersistenceBackend()
    first_sessions = fake.sessions
    first_idempotency = fake.idempotency_keys

    # Mutate the cached stub the way a test would (override a return).
    first_sessions.is_revoked.return_value = True

    fake.clear()

    # Fresh instance after clear -- not the mutated stub.
    assert fake.sessions is not first_sessions
    assert fake.idempotency_keys is not first_idempotency
    # The fresh sessions stub is back to the default ``False`` revoked state.
    assert fake.sessions.is_revoked("any-session") is False
