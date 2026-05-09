"""Tests for rollback handler protocol and default implementations."""

import pytest
from pydantic import JsonValue

from synthorg.core.types import NotBlankStr
from synthorg.meta.models import RollbackOperation
from synthorg.meta.rollout.inverse_dispatch import (
    ArchitectureMutator,
    CodeMutator,
    ConfigMutator,
    PromptMutator,
    RestorePromptHandler,
    RevertArchitectureHandler,
    RevertCodeHandler,
    RevertConfigHandler,
    RollbackHandler,
    default_rollback_handlers,
)

pytestmark = pytest.mark.unit


class _SpyConfigMutator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def set(self, *, path: str, value: object) -> None:
        self.calls.append((path, value))


class _SpyPromptMutator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def restore_principle(self, *, scope: str, text: str) -> None:
        self.calls.append((scope, text))


class _SpyArchMutator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def restore(self, *, target: str, previous_value: object) -> None:
        self.calls.append((target, previous_value))


class _SpyCodeMutator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def revert_file(self, *, path: str, content: str) -> None:
        self.calls.append((path, content))


def _op(
    *,
    op_type: str,
    target: str,
    previous_value: JsonValue = None,
    description: str = "test op",
) -> RollbackOperation:
    return RollbackOperation(
        operation_type=NotBlankStr(op_type),
        target=NotBlankStr(target),
        previous_value=previous_value,
        description=NotBlankStr(description),
    )


class TestRevertConfigHandler:
    async def test_sets_previous_value_at_target(self) -> None:
        mutator = _SpyConfigMutator()
        handler = RevertConfigHandler(mutator=mutator)
        op = _op(
            op_type="revert_config", target="budget.monthly_eur", previous_value=100
        )
        changes = await handler.revert(op)
        assert changes == 1
        assert mutator.calls == [("budget.monthly_eur", 100)]

    async def test_is_a_handler(self) -> None:
        assert isinstance(
            RevertConfigHandler(mutator=_SpyConfigMutator()),
            RollbackHandler,
        )


class TestRestorePromptHandler:
    async def test_restores_principle_for_scope(self) -> None:
        mutator = _SpyPromptMutator()
        handler = RestorePromptHandler(mutator=mutator)
        op = _op(
            op_type="restore_prompt",
            target="all",
            previous_value="Be concise.",
        )
        changes = await handler.revert(op)
        assert changes == 1
        assert mutator.calls == [("all", "Be concise.")]

    async def test_rejects_non_string_previous_value(self) -> None:
        mutator = _SpyPromptMutator()
        handler = RestorePromptHandler(mutator=mutator)
        op = _op(
            op_type="restore_prompt",
            target="all",
            previous_value=123,
        )
        with pytest.raises(ValueError, match="previous_value"):
            await handler.revert(op)


class TestRevertArchitectureHandler:
    async def test_restores_target_with_payload(self) -> None:
        mutator = _SpyArchMutator()
        handler = RevertArchitectureHandler(mutator=mutator)
        op = _op(
            op_type="revert_architecture",
            target="engineering.senior_eng",
            previous_value={"title": "Senior Engineer"},
        )
        changes = await handler.revert(op)
        assert changes == 1
        assert mutator.calls == [
            ("engineering.senior_eng", {"title": "Senior Engineer"}),
        ]


class TestRevertCodeHandler:
    async def test_reverts_file_to_previous_content(self) -> None:
        mutator = _SpyCodeMutator()
        handler = RevertCodeHandler(mutator=mutator)
        op = _op(
            op_type="revert_code",
            target="src/example.py",
            previous_value="print('hi')\n",
        )
        changes = await handler.revert(op)
        assert changes == 1
        assert mutator.calls == [("src/example.py", "print('hi')\n")]

    async def test_missing_content_raises(self) -> None:
        mutator = _SpyCodeMutator()
        handler = RevertCodeHandler(mutator=mutator)
        op = _op(
            op_type="revert_code",
            target="src/example.py",
            previous_value=None,
        )
        with pytest.raises(ValueError, match="previous_value"):
            await handler.revert(op)


class TestDefaultRollbackHandlers:
    def test_registers_all_known_operation_types(self) -> None:
        handlers = default_rollback_handlers(
            config=_SpyConfigMutator(),
            prompt=_SpyPromptMutator(),
            architecture=_SpyArchMutator(),
            code=_SpyCodeMutator(),
        )
        assert set(handlers.keys()) == {
            NotBlankStr("revert_config"),
            NotBlankStr("restore_prompt"),
            NotBlankStr("revert_architecture"),
            NotBlankStr("revert_code"),
        }
        for handler in handlers.values():
            assert isinstance(handler, RollbackHandler)

    def test_config_mutator_protocol(self) -> None:
        assert isinstance(_SpyConfigMutator(), ConfigMutator)

    def test_prompt_mutator_protocol(self) -> None:
        assert isinstance(_SpyPromptMutator(), PromptMutator)

    def test_architecture_mutator_protocol(self) -> None:
        assert isinstance(_SpyArchMutator(), ArchitectureMutator)

    def test_code_mutator_protocol(self) -> None:
        assert isinstance(_SpyCodeMutator(), CodeMutator)
