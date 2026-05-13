"""RoutedArchitectureMutator dispatches by target-type prefix."""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.meta.errors import (
    RollbackMutationDeniedError,
    UnknownArchitectureTargetError,
)
from synthorg.meta.rollout.mutators import RoutedArchitectureMutator

pytestmark = pytest.mark.unit


class TestRoutedArchitectureMutator:
    async def test_dispatches_to_registered_adapter(self) -> None:
        adapter = AsyncMock()
        mutator = RoutedArchitectureMutator({"role": adapter})

        await mutator.restore(target="role:agent-007", previous_value={"k": "v"})

        adapter.assert_awaited_once_with("agent-007", {"k": "v"})

    async def test_register_handler_replaces_existing(self) -> None:
        first = AsyncMock()
        second = AsyncMock()
        mutator = RoutedArchitectureMutator({"role": first})
        mutator.register_handler("role", second)

        await mutator.restore(target="role:bob", previous_value=None)

        first.assert_not_awaited()
        second.assert_awaited_once_with("bob", None)

    async def test_compound_target_id_preserved(self) -> None:
        """``workflow:wf-123:v4`` keeps the version in the tail."""
        adapter = AsyncMock()
        mutator = RoutedArchitectureMutator({"workflow": adapter})

        await mutator.restore(target="workflow:wf-123:v4", previous_value=None)

        adapter.assert_awaited_once_with("wf-123:v4", None)

    async def test_unknown_prefix_raises(self) -> None:
        mutator = RoutedArchitectureMutator({"role": AsyncMock()})

        with pytest.raises(
            UnknownArchitectureTargetError,
            match="no adapter registered",
        ):
            await mutator.restore(
                target="department:engineering",
                previous_value={},
            )

    async def test_malformed_target_raises(self) -> None:
        mutator = RoutedArchitectureMutator({"role": AsyncMock()})

        with pytest.raises(
            UnknownArchitectureTargetError,
            match=r"type.*id",
        ):
            await mutator.restore(
                target="not-a-prefix",
                previous_value=None,
            )

    async def test_adapter_failure_wrapped_as_denied(self) -> None:
        async def failing(_target: str, _value: Any) -> None:
            msg = "schema mismatch"
            raise RuntimeError(msg)

        mutator = RoutedArchitectureMutator({"role": failing})

        with pytest.raises(
            RollbackMutationDeniedError,
            match="adapter for 'role' failed",
        ):
            await mutator.restore(target="role:bob", previous_value={})
