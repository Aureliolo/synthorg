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
from tests._shared.bash import resolve_bash
from tests._shared.benchmark import FIXTURE_SOURCE, FakeCapabilityBenchmarkScoreProvider
from tests._shared.build_app import build_test_app
from tests._shared.capturing_logger import CapturingErrorLogger
from tests._shared.companies import make_company
from tests._shared.connection_catalog import (
    InMemorySecretBackend,
    make_in_memory_catalog,
)
from tests._shared.coordination_wiring import wire_decomposition_model
from tests._shared.fake_clock import FakeClock
from tests._shared.fake_docker import FakeDockerClient
from tests._shared.fake_sandbox import FakeSandbox
from tests._shared.ids import as_pk, as_uuid, coerce_id, sid
from tests._shared.initiative_doubles import RecordingReplanTrigger
from tests._shared.json_types import AsgiDict, JsonDict
from tests._shared.loop_async_client import LoopAsyncClient
from tests._shared.meeting_protocols import (
    pin_protocol,
    pinned_protocol_registry,
)
from tests._shared.mock_of import mock_of
from tests._shared.model_binding import (
    TEST_MODEL_ID,
    TEST_PROVIDER,
    bound_model,
    bound_ref,
    connections,
    model_ref_resolver,
    one_connection,
)
from tests._shared.offsetless_tz import OFFSETLESS_TZ, OffsetlessTz
from tests._shared.process_doubles import FakeCommandResult, FakeProcess
from tests._shared.recall import recall_request
from tests._shared.settings_fake import FakeSettingsService
from tests._shared.work_pipeline import (
    StubWorkPipeline,
    make_pipeline_result,
    task_from_work_item,
)

__all__ = [
    "FIXTURE_SOURCE",
    "OFFSETLESS_TZ",
    "TEST_MODEL_ID",
    "TEST_PROVIDER",
    "AsgiDict",
    "CapturingErrorLogger",
    "FakeCapabilityBenchmarkScoreProvider",
    "FakeClock",
    "FakeCommandResult",
    "FakeDockerClient",
    "FakeProcess",
    "FakeSandbox",
    "FakeSettingsService",
    "InMemorySecretBackend",
    "JsonDict",
    "LoopAsyncClient",
    "OffsetlessTz",
    "RecordingReplanTrigger",
    "StubWorkPipeline",
    "as_pk",
    "as_uuid",
    "bound_model",
    "bound_ref",
    "build_test_app",
    "coerce_id",
    "connections",
    "make_app_state",
    "make_company",
    "make_in_memory_catalog",
    "make_pipeline_result",
    "mock_of",
    "model_ref_resolver",
    "one_connection",
    "pin_protocol",
    "pinned_protocol_registry",
    "recall_request",
    "resolve_bash",
    "sid",
    "task_from_work_item",
    "wire_decomposition_model",
]
