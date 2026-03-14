"""Tests for file system tool helpers."""

import pytest

from synthorg.tools.file_system._base_fs_tool import _map_os_error

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("exc", "expected_key", "expected_msg_fragment"),
    [
        (
            FileNotFoundError("gone"),
            "not_found",
            "File not found: /workspace/missing.txt",
        ),
        (
            IsADirectoryError("is dir"),
            "is_directory",
            "Path is a directory, not a file: /workspace/missing.txt",
        ),
        (
            PermissionError("denied"),
            "permission_denied",
            "Permission denied: /workspace/missing.txt",
        ),
        (
            OSError("disk full"),
            "os_error",
            "OS error reading file '/workspace/missing.txt': disk full",
        ),
    ],
    ids=["file_not_found", "is_directory", "permission_denied", "generic_os"],
)
def test_map_os_error(
    exc: OSError,
    expected_key: str,
    expected_msg_fragment: str,
) -> None:
    key, msg = _map_os_error(exc, "/workspace/missing.txt", "reading")
    assert key == expected_key
    assert msg == expected_msg_fragment
