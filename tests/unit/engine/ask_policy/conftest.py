"""Shared fixtures for the ask-policy unit tests.

The ask-policy provider is a process-global ambient singleton (set at boot and
on a settings change), so a test that binds one must not leak it into a sibling
asserting the unbound default.

Teardown restores whatever was bound *before* the test rather than forcing
``None``: under the session-scoped app fixture in ``tests/unit/api`` a real
provider may be bound on the same xdist worker, and forcing ``None`` would stomp
it for every later test on that worker.
"""

from collections.abc import Iterator

import pytest

from synthorg.engine.ask_policy.provider import (
    current_ask_policy_provider,
    set_ask_policy_provider,
)


@pytest.fixture(autouse=True)
def _reset_ask_policy_ambient() -> Iterator[None]:
    previous = current_ask_policy_provider()
    set_ask_policy_provider(None)
    try:
        yield
    finally:
        set_ask_policy_provider(previous)
