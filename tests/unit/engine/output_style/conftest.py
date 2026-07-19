"""Shared fixtures for the output-style policy unit tests.

The soft-layer house-style provider and the hard-layer policy service are both
process-global ambient singletons (set at boot / on a settings change). A test
that binds one must not leak it into a sibling test that asserts the unbound
default, so reset both to a clean baseline before every test in this package.

Teardown restores whatever was bound *before* the test (which, under the
session-scoped app fixture in ``tests/unit/api``, may be a real service on the
same xdist worker) rather than forcing ``None`` -- forcing ``None`` on teardown
would stomp that real binding for every later test in the worker.
"""

from collections.abc import Iterator

import pytest

from synthorg.engine.output_style.provider import (
    current_house_style_provider,
    set_house_style_provider,
)
from synthorg.engine.output_style.service import (
    current_output_policy_service,
    set_output_policy_service,
)


@pytest.fixture(autouse=True)
def _reset_output_style_ambient() -> Iterator[None]:
    prev_provider = current_house_style_provider()
    prev_service = current_output_policy_service()
    set_house_style_provider(None)
    set_output_policy_service(None)
    try:
        yield
    finally:
        set_house_style_provider(prev_provider)
        set_output_policy_service(prev_service)
