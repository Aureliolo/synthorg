"""Conformance tests for ``ModelToolCallSignalRepository``."""

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.persistence.model_tool_call_signal_protocol import (
    ModelToolCallSignal,
)
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration


def _signal(
    *,
    provider: str = "example-provider",
    model: str = "example-expert-001",
    score: float = 2.0,
    decayed_at: float = 1_700_000_000.0,
) -> ModelToolCallSignal:
    return ModelToolCallSignal(
        provider_name=NotBlankStr(provider),
        model_id=NotBlankStr(model),
        failure_score=score,
        decayed_at=decayed_at,
    )


class TestModelToolCallSignalRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        await backend.model_tool_call_signals.save(_signal())

        result = await backend.model_tool_call_signals.get(
            (NotBlankStr("example-provider"), NotBlankStr("example-expert-001")),
        )
        assert result is not None
        assert result.failure_score == pytest.approx(2.0)
        assert result.decayed_at == pytest.approx(1_700_000_000.0)

    async def test_save_upserts(self, backend: PersistenceBackend) -> None:
        await backend.model_tool_call_signals.save(_signal(score=1.0))
        await backend.model_tool_call_signals.save(_signal(score=5.5))

        result = await backend.model_tool_call_signals.get(
            (NotBlankStr("example-provider"), NotBlankStr("example-expert-001")),
        )
        assert result is not None
        assert result.failure_score == pytest.approx(5.5)

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        result = await backend.model_tool_call_signals.get(
            (NotBlankStr("ghost-provider"), NotBlankStr("ghost-model")),
        )
        assert result is None

    async def test_delete_existing(self, backend: PersistenceBackend) -> None:
        await backend.model_tool_call_signals.save(_signal())

        deleted = await backend.model_tool_call_signals.delete(
            (NotBlankStr("example-provider"), NotBlankStr("example-expert-001")),
        )
        assert deleted is True
        gone = await backend.model_tool_call_signals.get(
            (NotBlankStr("example-provider"), NotBlankStr("example-expert-001")),
        )
        assert gone is None

    async def test_delete_missing(self, backend: PersistenceBackend) -> None:
        deleted = await backend.model_tool_call_signals.delete(
            (NotBlankStr("ghost-provider"), NotBlankStr("ghost-model")),
        )
        assert deleted is False

    async def test_list_items_in_key_order(self, backend: PersistenceBackend) -> None:
        await backend.model_tool_call_signals.save(
            _signal(provider="li-zeta", model="li-z2")
        )
        await backend.model_tool_call_signals.save(
            _signal(provider="li-alpha", model="li-a2")
        )

        results = await backend.model_tool_call_signals.list_items()
        scoped = [
            (r.provider_name, r.model_id)
            for r in results
            if r.provider_name.startswith("li-")
        ]
        assert scoped == [("li-alpha", "li-a2"), ("li-zeta", "li-z2")]
