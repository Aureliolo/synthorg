"""Tests for org memory error hierarchy."""

import pytest

from synthorg.memory.org.errors import (
    OrgMemoryAccessDeniedError,
    OrgMemoryConfigError,
    OrgMemoryConnectionError,
    OrgMemoryError,
    OrgMemoryQueryError,
    OrgMemoryWriteError,
)

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestOrgMemoryErrorHierarchy:
    """All org memory errors inherit from OrgMemoryError."""

    @pytest.mark.parametrize(
        "error_cls",
        [
            OrgMemoryConnectionError,
            OrgMemoryWriteError,
            OrgMemoryQueryError,
            OrgMemoryAccessDeniedError,
            OrgMemoryConfigError,
        ],
    )
    def test_inherits_from_base(
        self,
        error_cls: type[OrgMemoryError],
    ) -> None:
        assert issubclass(error_cls, OrgMemoryError)

    @pytest.mark.parametrize(
        "error_cls",
        [
            OrgMemoryError,
            OrgMemoryConnectionError,
            OrgMemoryWriteError,
            OrgMemoryQueryError,
            OrgMemoryAccessDeniedError,
            OrgMemoryConfigError,
        ],
    )
    def test_catchable_with_base(
        self,
        error_cls: type[OrgMemoryError],
    ) -> None:
        msg = "test"
        with pytest.raises(OrgMemoryError):
            raise error_cls(msg)

    @pytest.mark.parametrize(
        "error_cls",
        [
            OrgMemoryError,
            OrgMemoryConnectionError,
            OrgMemoryWriteError,
            OrgMemoryQueryError,
            OrgMemoryAccessDeniedError,
            OrgMemoryConfigError,
        ],
    )
    def test_message_preserved(
        self,
        error_cls: type[OrgMemoryError],
    ) -> None:
        detail = "detailed error info"
        err = error_cls(detail)
        assert str(err) == detail

    def test_all_inherit_from_exception(self) -> None:
        assert issubclass(OrgMemoryError, Exception)
