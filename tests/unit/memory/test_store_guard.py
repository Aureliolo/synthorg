"""Unit tests for memory store guard (non-inferable tag validation)."""

import pytest
import structlog.testing

from synthorg.core.enums import MemoryCategory
from synthorg.memory.filter import NON_INFERABLE_TAG
from synthorg.memory.models import MemoryMetadata, MemoryStoreRequest
from synthorg.memory.store_guard import validate_memory_tags
from synthorg.observability.events.memory import (
    MEMORY_FILTER_STORE_MISSING_TAG,
)

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestValidateMemoryTags:
    """Tests for the validate_memory_tags advisory guard."""

    def test_tagged_request_no_warning(self) -> None:
        """Request with non-inferable tag produces no warning."""
        request = MemoryStoreRequest(
            category=MemoryCategory.EPISODIC,
            content="important fact",
            metadata=MemoryMetadata(tags=(NON_INFERABLE_TAG,)),
        )
        with structlog.testing.capture_logs() as logs:
            validate_memory_tags(request)

        warning_events = [
            e for e in logs if e["event"] == MEMORY_FILTER_STORE_MISSING_TAG
        ]
        assert len(warning_events) == 0

    def test_untagged_request_logs_warning(self) -> None:
        """Request without non-inferable tag logs a warning."""
        request = MemoryStoreRequest(
            category=MemoryCategory.SEMANTIC,
            content="some knowledge",
        )
        with structlog.testing.capture_logs() as logs:
            validate_memory_tags(request)

        warning_events = [
            e for e in logs if e["event"] == MEMORY_FILTER_STORE_MISSING_TAG
        ]
        assert len(warning_events) == 1

    def test_other_tags_still_warns(self) -> None:
        """Request with other tags but missing non-inferable tag warns."""
        request = MemoryStoreRequest(
            category=MemoryCategory.PROCEDURAL,
            content="how to deploy",
            metadata=MemoryMetadata(tags=("deployment", "ops")),
        )
        with structlog.testing.capture_logs() as logs:
            validate_memory_tags(request)

        warning_events = [
            e for e in logs if e["event"] == MEMORY_FILTER_STORE_MISSING_TAG
        ]
        assert len(warning_events) == 1

    def test_store_never_blocked(self) -> None:
        """validate_memory_tags never raises — advisory only."""
        request = MemoryStoreRequest(
            category=MemoryCategory.EPISODIC,
            content="any content",
        )
        # Should complete without exception.
        validate_memory_tags(request)
