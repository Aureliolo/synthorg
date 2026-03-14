"""Tests for memory error hierarchy."""

import pytest

from synthorg.memory.errors import (
    MemoryCapabilityError,
    MemoryConfigError,
    MemoryConnectionError,
    MemoryError,  # noqa: A004
    MemoryNotFoundError,
    MemoryRetrievalError,
    MemoryStoreError,
)

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestMemoryErrorHierarchy:
    """All memory errors inherit from MemoryError."""

    @pytest.mark.parametrize(
        "error_cls",
        [
            MemoryConnectionError,
            MemoryStoreError,
            MemoryRetrievalError,
            MemoryNotFoundError,
            MemoryConfigError,
            MemoryCapabilityError,
        ],
    )
    def test_inherits_from_memory_error(self, error_cls: type[MemoryError]) -> None:
        assert issubclass(error_cls, MemoryError)

    @pytest.mark.parametrize(
        "error_cls",
        [
            MemoryError,
            MemoryConnectionError,
            MemoryStoreError,
            MemoryRetrievalError,
            MemoryNotFoundError,
            MemoryConfigError,
            MemoryCapabilityError,
        ],
    )
    def test_catchable_with_base(self, error_cls: type[MemoryError]) -> None:
        msg = "test message"
        with pytest.raises(MemoryError):
            raise error_cls(msg)

    @pytest.mark.parametrize(
        "error_cls",
        [
            MemoryError,
            MemoryConnectionError,
            MemoryStoreError,
            MemoryRetrievalError,
            MemoryNotFoundError,
            MemoryConfigError,
            MemoryCapabilityError,
        ],
    )
    def test_message_preserved(self, error_cls: type[MemoryError]) -> None:
        err = error_cls("detailed error info")
        assert str(err) == "detailed error info"

    def test_all_inherit_from_exception(self) -> None:
        assert issubclass(MemoryError, Exception)
