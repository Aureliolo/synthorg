"""Shared fixtures for the output-style policy unit tests.

The soft-layer house-style provider and the hard-layer policy service are both
process-global ambient singletons (set at boot / on a settings change). A test
that binds one must not leak it into a sibling test that asserts the unbound
default, so reset both around every test in this package.
"""

from collections.abc import Iterator

import pytest

from synthorg.engine.output_style.provider import set_house_style_provider
from synthorg.engine.output_style.service import set_output_policy_service


@pytest.fixture(autouse=True)
def _reset_output_style_ambient() -> Iterator[None]:
    set_house_style_provider(None)
    set_output_policy_service(None)
    yield
    set_house_style_provider(None)
    set_output_policy_service(None)
