"""Shared test helpers usable from any tests/* subtree.

These helpers exist outside ``tests/unit/`` and ``tests/integration/``
so the same utility (``FakeClock``, ``mock_of``, ...) can be imported
from any test file regardless of marker. Tests that exercise the
helpers themselves (e.g. ``test_mock_of.py``) live alongside them
here; the mock-spec gate excludes the package via
``scripts/check_mock_spec.py``'s ``_iter_test_files`` so the helpers
are not scanned for the bare-mock convention they implement.
"""

from tests._shared.app_state import make_app_state
from tests._shared.build_app import build_test_app
from tests._shared.capturing_logger import CapturingErrorLogger
from tests._shared.fake_clock import FakeClock
from tests._shared.loop_async_client import LoopAsyncClient
from tests._shared.mock_of import mock_of
from tests._shared.trust import NoOpTrustStrategy

__all__ = [
    "CapturingErrorLogger",
    "FakeClock",
    "LoopAsyncClient",
    "NoOpTrustStrategy",
    "build_test_app",
    "make_app_state",
    "mock_of",
]
