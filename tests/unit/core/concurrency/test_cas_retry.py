"""Tests for :class:`synthorg.core.concurrency.CASRetryHandler`."""

import pytest
import structlog.testing

from synthorg.core.concurrency import CASRetryHandler
from synthorg.core.domain_errors import VersionConflictError

pytestmark = pytest.mark.unit


class TestCASRetryHandler:
    """Behavioural tests for the CAS retry helper."""

    async def test_succeeds_on_first_attempt(self) -> None:
        read_calls = 0
        write_calls = 0

        async def read() -> tuple[str, str]:
            nonlocal read_calls
            read_calls += 1
            return "new", "v1"

        async def write(value: str, version: str) -> None:
            nonlocal write_calls
            write_calls += 1
            assert value == "new"
            assert version == "v1"

        handler = CASRetryHandler(resource="test")
        result = await handler.execute(read, write)

        assert result == "new"
        assert read_calls == 1
        assert write_calls == 1

    async def test_retries_once_then_succeeds(self) -> None:
        read_calls = 0
        write_calls = 0

        async def read() -> tuple[str, str]:
            nonlocal read_calls
            read_calls += 1
            return f"v{read_calls}", f"ver{read_calls}"

        async def write(_value: str, _version: str) -> None:
            nonlocal write_calls
            write_calls += 1
            if write_calls == 1:
                msg = "stale"
                raise VersionConflictError(msg)

        handler = CASRetryHandler(resource="agents")
        with structlog.testing.capture_logs() as events:
            result = await handler.execute(read, write)

        assert result == "v2"
        assert read_calls == 2
        assert write_calls == 2
        debug_events = [e for e in events if e.get("log_level") == "debug"]
        assert any(
            e.get("event") == "api.concurrency.conflict"
            and e.get("attempt") == 1
            and e.get("max_attempts") == 2
            and e.get("resource") == "agents"
            for e in debug_events
        )

    async def test_raises_after_max_attempts(self) -> None:
        async def read() -> tuple[str, str]:
            return "x", "v"

        async def write(_value: str, _version: str) -> None:
            msg = "always stale"
            raise VersionConflictError(msg)

        handler = CASRetryHandler(resource="agents", max_attempts=3)
        with (
            structlog.testing.capture_logs() as events,
            pytest.raises(VersionConflictError),
        ):
            await handler.execute(read, write)

        warning_events = [
            e
            for e in events
            if e.get("log_level") == "warning"
            and e.get("event") == "api.concurrency.conflict"
        ]
        assert len(warning_events) == 1
        assert warning_events[0].get("attempts") == 3
        assert warning_events[0].get("resource") == "agents"

    async def test_non_conflict_exception_propagates_without_retry(self) -> None:
        read_calls = 0

        async def read() -> tuple[str, str]:
            nonlocal read_calls
            read_calls += 1
            return "x", "v"

        async def write(_value: str, _version: str) -> None:
            msg = "boom"
            raise RuntimeError(msg)

        handler = CASRetryHandler(resource="agents", max_attempts=5)
        with pytest.raises(RuntimeError, match="boom"):
            await handler.execute(read, write)

        assert read_calls == 1

    async def test_validation_error_in_read_propagates_immediately(self) -> None:
        read_calls = 0

        async def read() -> tuple[str, str]:
            nonlocal read_calls
            read_calls += 1
            msg = "invalid"
            raise ValueError(msg)

        async def write(_value: str, _version: str) -> None:
            pytest.fail("write should not be called when read raises")

        handler = CASRetryHandler(resource="agents", max_attempts=3)
        with (
            structlog.testing.capture_logs() as events,
            pytest.raises(ValueError, match="invalid"),
        ):
            await handler.execute(read, write)

        assert read_calls == 1
        # Read errors are propagated immediately and must NOT be
        # treated as concurrency conflicts; verify no retry log
        # was emitted.  A future regression that wraps ``await
        # read()`` inside the retry-on-VersionConflictError branch
        # would silently turn deterministic validation failures
        # into retry storms; this assertion catches that.
        retry_events = [
            e for e in events if e.get("event") == "api.concurrency.conflict"
        ]
        assert retry_events == []

    async def test_max_attempts_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="max_attempts must be >= 1"):
            CASRetryHandler(resource="x", max_attempts=0)

    async def test_max_attempts_one_does_not_retry(self) -> None:
        write_calls = 0

        async def read() -> tuple[str, str]:
            return "x", "v"

        async def write(_value: str, _version: str) -> None:
            nonlocal write_calls
            write_calls += 1
            msg = "stale"
            raise VersionConflictError(msg)

        handler = CASRetryHandler(resource="agents", max_attempts=1)
        with pytest.raises(VersionConflictError):
            await handler.execute(read, write)

        assert write_calls == 1

    async def test_resource_label_in_log_records(self) -> None:
        async def read() -> tuple[str, str]:
            return "x", "v"

        async def write(_value: str, _version: str) -> None:
            msg = "stale"
            raise VersionConflictError(msg)

        handler = CASRetryHandler(resource="my_resource", max_attempts=2)
        with (
            structlog.testing.capture_logs() as events,
            pytest.raises(VersionConflictError),
        ):
            await handler.execute(read, write)

        relevant = [e for e in events if e.get("event") == "api.concurrency.conflict"]
        assert relevant, "expected at least one CAS retry event"
        for e in relevant:
            assert e.get("resource") == "my_resource"

    async def test_properties_expose_constructor_args(self) -> None:
        handler = CASRetryHandler(resource="r", max_attempts=7)
        assert handler.resource == "r"
        assert handler.max_attempts == 7
