"""Unit tests for the gated-write helpers' degraded and empty branches.

The happy path runs end-to-end in ``test_supersession_flow``. What that
flow cannot reach is the defensive behaviour: a store that fails the
similarity read, and a retirement asked to retire something that is no
longer there. Both decide whether a real memory is silently lost, so
they are pinned here directly.
"""

from datetime import UTC, datetime

import pytest
from structlog.testing import capture_logs

from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.self_editing_write import comparable_entries, retire_superseded
from synthorg.memory.write_gate import SUPERSEDED_BY_TAG_PREFIX, SUPERSEDED_TAG
from synthorg.observability.events.memory import MEMORY_WRITE_GATE_DEGRADED
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_AGENT = NotBlankStr("agent-1")


def _entry(entry_id: str, tags: tuple[str, ...] = ()) -> MemoryEntry:
    return MemoryEntry(
        id=NotBlankStr(entry_id),
        agent_id=_AGENT,
        category=MemoryCategory.SEMANTIC,
        content=NotBlankStr("a stored lesson"),
        metadata=MemoryMetadata(tags=tuple(NotBlankStr(t) for t in tags)),
        created_at=datetime(2026, 7, 20, tzinfo=UTC),
    )


class TestComparableEntriesFailOpen:
    """A failed similarity read must risk a duplicate, never a lost write."""

    async def test_non_critical_failure_returns_empty_and_warns(self) -> None:
        async def _boom(*_args: object, **_kwargs: object) -> tuple[MemoryEntry, ...]:
            msg = "index unavailable"
            raise ValueError(msg)

        backend = mock_of[MemoryBackend](retrieve=_boom)

        with capture_logs() as logs:
            result = await comparable_entries(
                backend,
                _AGENT,
                NotBlankStr("a candidate lesson"),
                MemoryCategory.SEMANTIC,
                limit=5,
            )

        assert result == ()
        assert any(e["event"] == MEMORY_WRITE_GATE_DEGRADED for e in logs)

    async def test_memory_error_is_re_raised(self) -> None:
        async def _oom(*_args: object, **_kwargs: object) -> tuple[MemoryEntry, ...]:
            raise MemoryError

        backend = mock_of[MemoryBackend](retrieve=_oom)

        with pytest.raises(MemoryError):
            await comparable_entries(
                backend,
                _AGENT,
                NotBlankStr("a candidate lesson"),
                MemoryCategory.SEMANTIC,
                limit=5,
            )


class TestRetireSuperseded:
    """Retirement must report honestly when there is nothing to retire."""

    async def test_absent_entry_returns_false(self) -> None:
        async def _missing(*_args: object, **_kwargs: object) -> None:
            return None

        backend = mock_of[MemoryBackend](get=_missing)

        retired = await retire_superseded(
            backend, _AGENT, NotBlankStr("gone"), replacement_id="new"
        )

        assert retired is False

    async def test_update_returning_none_returns_false(self) -> None:
        async def _get(*_args: object, **_kwargs: object) -> MemoryEntry:
            return _entry("old")

        async def _update(*_args: object, **_kwargs: object) -> None:
            return None

        backend = mock_of[MemoryBackend](get=_get, update=_update)

        retired = await retire_superseded(
            backend, _AGENT, NotBlankStr("old"), replacement_id="new"
        )

        assert retired is False

    async def test_successful_retirement_tags_both_marks(self) -> None:
        captured: dict[str, object] = {}

        async def _get(*_args: object, **_kwargs: object) -> MemoryEntry:
            return _entry("old")

        async def _update(
            agent_id: object, memory_id: object, request: object
        ) -> MemoryEntry:
            captured["tags"] = request.metadata.tags  # type: ignore[attr-defined]
            return _entry("old")

        backend = mock_of[MemoryBackend](get=_get, update=_update)

        retired = await retire_superseded(
            backend, _AGENT, NotBlankStr("old"), replacement_id="new"
        )

        assert retired is True
        tags = captured["tags"]
        assert isinstance(tags, tuple)
        assert SUPERSEDED_TAG in tags
        assert f"{SUPERSEDED_BY_TAG_PREFIX}new" in tags
