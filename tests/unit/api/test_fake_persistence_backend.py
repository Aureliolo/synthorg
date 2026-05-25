"""Runtime parity check: ``FakePersistenceBackend`` satisfies ``PersistenceBackend``.

``PersistenceBackend`` is ``@runtime_checkable``, so ``isinstance`` here
asserts the fake exposes every protocol member by name. Drift surfaces
as a single, named test failure instead of a 75-error mypy cascade at
the typed-boundary call sites where the fake is consumed.
"""

import pytest

from synthorg.persistence.protocol import PersistenceBackend
from tests.unit.api.fakes_backend import FakePersistenceBackend

pytestmark = pytest.mark.unit


def test_fake_persistence_backend_satisfies_protocol() -> None:
    fake = FakePersistenceBackend()
    assert isinstance(fake, PersistenceBackend)
